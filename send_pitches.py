#!/usr/bin/env python3
"""
Bulk outreach helper for legitimate music pitching.

Reads a CSV of station contacts and sends a personalized email to each row
using your SMTP account. Includes dry-run mode, throttling, and per-recipient
logging to make the run auditable.
"""

from __future__ import annotations

import argparse
import mimetypes
import csv
import html.parser
import io
import os
import ssl
import smtplib
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from string import Template
from collections.abc import Callable, Mapping
from threading import Event


DEFAULT_SUBJECT = "Music Pitch: ${song_title} - ${artist_name}"
DEFAULT_BODY = """Hi ${music_director_or_station},

I hope this email finds you well. My name is ${artist_name}, and I’m an independent artist.

I’m reaching out because I believe my track "${song_title}" would resonate with your listeners at ${station_name}.
The song is a ${genre_description}, and it explores ${theme_description}.

Streaming link:
${stream_url}

A bit about me:

- ${artist_blurb_1}
- ${artist_blurb_2}
- ${artist_blurb_3}

If you’re interested, I can send a full press kit as well.

Thank you for your time and for supporting independent music.

Best regards,
${artist_name}
Spotify: ${spotify_artist_url}
YouTube: ${youtube_url}
Single: ${stream_url}
"""

MAX_SAFE_ATTACHMENT_BYTES = 18 * 1024 * 1024
DEFAULT_DRIVE_UPLOAD_THRESHOLD_BYTES = 25 * 1024 * 1024
DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive.file",)


@dataclass(frozen=True)
class Config:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    from_name: str
    from_email: str
    use_ssl: bool
    dry_run: bool
    delay_seconds: float
    max_per_run: int | None
    input_csv: Path
    source_url: str | None
    sent_log_csv: Path
    body_template_path: Path | None
    subject_template: str
    attachment_paths: tuple[str, ...] = ()
    drive_client_secrets_path: Path | None = None
    drive_token_path: Path | None = None
    drive_folder_id: str | None = None
    drive_upload_threshold_bytes: int = DEFAULT_DRIVE_UPLOAD_THRESHOLD_BYTES
    drive_make_public: bool = True
    body_template_text: str | None = None
    subject_template_text: str | None = None


@dataclass(frozen=True)
class DriveUploadResult:
    file_name: str
    drive_file_id: str
    web_view_link: str


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Send personalized radio pitches from a CSV list.")
    parser.add_argument("--input", help="CSV file with station contacts.")
    parser.add_argument("--smtp-host", default=os.getenv("SMTP_HOST", "smtp.gmail.com"))
    parser.add_argument("--smtp-port", default=os.getenv("SMTP_PORT", "587"), type=int)
    parser.add_argument("--smtp-user", default=os.getenv("SMTP_USER"))
    parser.add_argument("--smtp-password", default=os.getenv("SMTP_PASSWORD"))
    parser.add_argument("--from-name", default=os.getenv("FROM_NAME", "Roman Tishkov"))
    parser.add_argument("--from-email", default=os.getenv("FROM_EMAIL"))
    parser.add_argument("--ssl", action="store_true", default=os.getenv("SMTP_SSL", "").lower() in {"1", "true", "yes"})
    parser.add_argument("--dry-run", action="store_true", help="Render messages without sending.")
    parser.add_argument("--delay", default=float(os.getenv("SEND_DELAY_SECONDS", "1.5")), type=float)
    parser.add_argument("--max-per-run", default=os.getenv("MAX_PER_RUN"), type=int)
    parser.add_argument("--sent-log", default="sent_log.csv", help="CSV file where sent recipients are recorded.")
    parser.add_argument("--body-template", help="Path to a custom body template file.")
    parser.add_argument("--subject-template", default=DEFAULT_SUBJECT)
    parser.add_argument("--source-url", help="Fetch stations from the public RadioReach site instead of a CSV.")
    parser.add_argument("--attach", action="append", default=[], help="Attachment file path. Repeat to add multiple files.")
    parser.add_argument("--drive-client-secrets", help="Google OAuth desktop client secrets JSON for Drive uploads.")
    parser.add_argument("--drive-token", help="Path to cached Google OAuth token JSON.")
    parser.add_argument("--drive-folder-id", help="Optional Google Drive folder ID for uploaded files.")
    parser.add_argument("--drive-threshold-mb", type=float, default=float(os.getenv("DRIVE_THRESHOLD_MB", "25")), help="Per-file size threshold in MB for Drive upload.")
    parser.add_argument("--drive-no-public", action="store_true", help="Do not make Drive uploads public.")
    ns = parser.parse_args()

    if not ns.input and not ns.source_url:
        parser.error("either --input or --source-url is required")

    if not ns.smtp_user and not ns.dry_run:
        parser.error("--smtp-user or SMTP_USER is required unless --dry-run is used")
    if not ns.smtp_password and not ns.dry_run:
        parser.error("--smtp-password or SMTP_PASSWORD is required unless --dry-run is used")
    if not ns.from_email:
        ns.from_email = ns.smtp_user or "test@example.com"

    return Config(
        smtp_host=ns.smtp_host,
        smtp_port=ns.smtp_port,
        smtp_user=ns.smtp_user or "",
        smtp_password=ns.smtp_password or "",
        from_name=ns.from_name,
        from_email=ns.from_email,
        use_ssl=ns.ssl,
        dry_run=ns.dry_run,
        delay_seconds=ns.delay,
        max_per_run=ns.max_per_run,
        input_csv=Path(ns.input) if ns.input else Path("stations.csv"),
        source_url=ns.source_url,
        sent_log_csv=Path(ns.sent_log),
        body_template_path=Path(ns.body_template) if ns.body_template else None,
        subject_template=ns.subject_template,
        attachment_paths=tuple(ns.attach or []),
        drive_client_secrets_path=Path(ns.drive_client_secrets) if ns.drive_client_secrets else None,
        drive_token_path=Path(ns.drive_token) if ns.drive_token else None,
        drive_folder_id=ns.drive_folder_id,
        drive_upload_threshold_bytes=max(int(ns.drive_threshold_mb * 1024 * 1024), 1),
        drive_make_public=not ns.drive_no_public,
    )


def load_template(path: Path | None, fallback: str) -> Template:
    if path:
        return Template(path.read_text(encoding="utf-8"))
    return Template(fallback)


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{size} B"


def attachment_total_bytes(attachment_paths: tuple[str, ...]) -> int:
    return sum(Path(attachment).stat().st_size for attachment in attachment_paths)


def validate_attachment_size_limit(
    attachment_paths: tuple[str, ...],
    *,
    limit_bytes: int = MAX_SAFE_ATTACHMENT_BYTES,
) -> None:
    total_bytes = attachment_total_bytes(attachment_paths)
    if total_bytes > limit_bytes:
        raise ValueError(
            "Attachments are too large for reliable SMTP delivery. "
            f"Total size is {format_bytes(total_bytes)}, but the safe limit is {format_bytes(limit_bytes)}. "
            "Gmail counts the encoded message size, so leave headroom or use links instead."
        )


def split_attachments_by_size(
    attachment_paths: tuple[str, ...],
    *,
    threshold_bytes: int = DEFAULT_DRIVE_UPLOAD_THRESHOLD_BYTES,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    small: list[Path] = []
    large: list[Path] = []
    for attachment in attachment_paths:
        path = Path(attachment)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Attachment not found: {path}")
        if path.stat().st_size >= threshold_bytes:
            large.append(path)
        else:
            small.append(path)
    return tuple(small), tuple(large)


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/javascript,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


class ScriptSrcExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.script_srcs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attr_map = dict(attrs)
        src = attr_map.get("src")
        if src:
            self.script_srcs.append(src)


def resolve_catalog_script_src(page_url: str) -> str:
    html = fetch_text(page_url)
    parser = ScriptSrcExtractor()
    parser.feed(html)
    for src in parser.script_srcs:
        if "/assets/" in src and src.endswith(".js"):
            return urllib.parse.urljoin(page_url, src)
    raise RuntimeError(f"Could not find station bundle on {page_url}")


def extract_catalog_csv_from_bundle(bundle_text: str) -> str:
    start = bundle_text.find("const wy=`")
    if start == -1:
        raise RuntimeError("Could not locate embedded station catalog in bundle")
    start += len("const wy=`")
    end = bundle_text.find("function IP()", start)
    if end == -1:
        end = bundle_text.find("function IP(", start)
    if end == -1:
        end = bundle_text.find("const IP=", start)
    if end == -1:
        raise RuntimeError("Could not locate end of embedded station catalog in bundle")
    return bundle_text[start:end].replace("\\r", "\r").replace("\\n", "\n")


def parse_catalog_csv(catalog_csv: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(catalog_csv))
    rows: list[dict[str, str]] = []
    for idx, row in enumerate(reader, start=2):
        normalized = {k.strip(): (v.strip() if isinstance(v, str) else "") for k, v in row.items() if k}
        email = normalized.get("Email", "")
        company = normalized.get("Company", "")
        if not email or not company:
            continue
        genres = [part.strip() for part in normalized.get("Genre", "").split("/") if part.strip()]
        compact_genres = ", ".join(genres[:3]) if genres else ""
        notes = normalized.get("Notes", "")
        rows.append(
            {
                "station_name": company,
                "music_director": normalized.get("Contact", ""),
                "email": email,
                "street1": normalized.get("Street 1", ""),
                "street2": normalized.get("Street 2", ""),
                "city": normalized.get("City", ""),
                "state": normalized.get("State", ""),
                "zip": normalized.get("Zip", ""),
                "phone": normalized.get("Phone", ""),
                "station_url": normalized.get("Website", ""),
                "genre_description": compact_genres or normalized.get("Genre", ""),
                "station_notes": notes,
                "country": normalized.get("Country", "United States"),
                "genres": genres,
            }
        )
    return rows


def load_contacts_from_source_url(page_url: str) -> list[dict[str, str]]:
    bundle_url = resolve_catalog_script_src(page_url)
    bundle_text = fetch_text(bundle_url)
    catalog_csv = extract_catalog_csv_from_bundle(bundle_text)
    return parse_catalog_csv(catalog_csv)


def read_contacts(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows: list[dict[str, str]] = []
        for idx, row in enumerate(reader, start=2):
            normalized = {k.strip(): (v.strip() if isinstance(v, str) else "") for k, v in row.items() if k}
            email = normalized.get("email", "")
            if not email:
                print(f"Skipping row {idx}: missing email", file=sys.stderr)
                continue
            rows.append(normalized)
        return rows


def load_sent_addresses(sent_log_csv: Path) -> set[str]:
    if not sent_log_csv.exists():
        return set()
    with sent_log_csv.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return {row.get("email", "").strip().lower() for row in reader if row.get("email")}


def append_sent_log(sent_log_csv: Path, row: Mapping[str, str]) -> None:
    need_header = not sent_log_csv.exists()
    with sent_log_csv.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "email", "station_name", "status"])
        if need_header:
            writer.writeheader()
        writer.writerow(row)
        f.flush()
        os.fsync(f.fileno())


def merge_context(
    row: Mapping[str, str],
    *,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    station_name = row.get("station_name") or row.get("station") or row.get("name") or row.get("email")
    music_director = row.get("music_director") or row.get("director") or row.get("contact_name") or "Music Director"
    context = {
        "artist_name": row.get("artist_name", "Roman Tishkov"),
        "song_title": row.get("song_title", "Burn the System"),
        "stream_url": row.get("stream_url", "https://open.spotify.com/album/5hHoVRrEfTD62Yrju9RNgl"),
        "spotify_artist_url": row.get("spotify_artist_url", "https://open.spotify.com/artist/54ukzXc5sUbZdKsUTDKJvY"),
        "youtube_url": row.get("youtube_url", "https://youtu.be/ykIasUhIAGI"),
        "station_name": station_name or "",
        "music_director": music_director,
        "music_director_or_station": music_director if music_director and music_director != "Music Director" else (station_name or "Music Director"),
        "genre_description": row.get("genre_description", "dark cinematic industrial rock / alternative track with a rebellious, high-energy atmosphere"),
        "theme_description": row.get("theme_description", "frustration with broken systems, social pressure, and the feeling of fighting back against control"),
        "artist_blurb_1": row.get("artist_blurb_1", "I create cinematic, dark and emotionally charged music blending alternative rock, gothic rock, industrial energy, dark pop and electronic elements."),
        "artist_blurb_2": row.get("artist_blurb_2", "My songs often explore themes of inner conflict, rebellion, freedom, personal transformation and the fight against fear or control."),
        "artist_blurb_3": row.get("artist_blurb_3", "As an independent artist, I build each release as a full visual and musical concept with strong storytelling and atmosphere."),
    }
    context.update({k: v for k, v in row.items() if k not in context})
    if overrides:
        context.update({k: v for k, v in overrides.items() if v is not None})
    return context


def render_text(template: Template | str, context: Mapping[str, str]) -> str:
    if isinstance(template, str):
        template = Template(template)
    return template.safe_substitute(context)


def _attach_files(msg: EmailMessage, attachment_paths: tuple[str, ...]) -> None:
    for attachment in attachment_paths:
        path = Path(attachment)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Attachment not found: {path}")
        data = path.read_bytes()
        mime_type, _ = mimetypes.guess_type(path.name)
        if mime_type:
            maintype, subtype = mime_type.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)


def _sleep_with_stop(seconds: float, stop_event: Event | None) -> bool:
    deadline = time.monotonic() + max(seconds, 0.0)
    while True:
        if stop_event and stop_event.is_set():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.25, remaining))


def _format_drive_link_section(results: list[DriveUploadResult]) -> str:
    lines = ["Files uploaded to Google Drive:"]
    for result in results:
        lines.append(f"- {result.file_name}: {result.web_view_link}")
    return "\n".join(lines)


def _load_drive_service(cfg: Config):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:  # pragma: no cover - dependency error path
        raise RuntimeError(
            "Google Drive support requires google-api-python-client, google-auth-httplib2, and google-auth-oauthlib."
        ) from exc

    if not cfg.drive_client_secrets_path:
        raise ValueError("Google Drive client secrets file is not configured.")

    token_path = cfg.drive_token_path or cfg.drive_client_secrets_path.with_name("drive_token.json")
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), DRIVE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(cfg.drive_client_secrets_path), DRIVE_SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return service, MediaFileUpload


def _upload_file_to_drive(
    service: object,
    media_file_upload: object,
    cfg: Config,
    path: Path,
) -> DriveUploadResult:
    metadata: dict[str, object] = {"name": path.name}
    if cfg.drive_folder_id:
        metadata["parents"] = [cfg.drive_folder_id]
    media = media_file_upload(str(path), resumable=True)
    created = service.files().create(body=metadata, media_body=media, fields="id,name,webViewLink,webContentLink").execute()
    file_id = str(created["id"])
    if cfg.drive_make_public:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()
        created = service.files().get(fileId=file_id, fields="id,name,webViewLink,webContentLink").execute()
    link = str(created.get("webViewLink") or created.get("webContentLink") or f"https://drive.google.com/file/d/{file_id}/view")
    return DriveUploadResult(file_name=path.name, drive_file_id=file_id, web_view_link=link)


def upload_file_to_drive(cfg: Config, path: Path) -> DriveUploadResult:
    service, media_file_upload = _load_drive_service(cfg)
    return _upload_file_to_drive(service, media_file_upload, cfg, path)


def make_message(
    cfg: Config,
    recipient: str,
    subject: str,
    body: str,
    *,
    attachment_paths: tuple[str, ...] = (),
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{cfg.from_name} <{cfg.from_email}>"
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment_paths:
        validate_attachment_size_limit(attachment_paths)
        _attach_files(msg, attachment_paths)
    return msg


def send_message(cfg: Config, msg: EmailMessage) -> None:
    try:
        if cfg.smtp_port == 465:
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=30) as server:
                server.login(cfg.smtp_user, cfg.smtp_password)
                server.send_message(msg)
            return

        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as server:
            server.ehlo()
            if cfg.use_ssl or cfg.smtp_port in {25, 587, 2525}:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            server.login(cfg.smtp_user, cfg.smtp_password)
            server.send_message(msg)
    except ssl.SSLError as exc:
        raise RuntimeError(
            f"SMTP security handshake failed for {cfg.smtp_host}:{cfg.smtp_port}. "
            "Use port 465 for implicit SSL or 587 with STARTTLS."
        ) from exc


def load_contacts(cfg: Config) -> list[dict[str, str]]:
    if cfg.source_url:
        return load_contacts_from_source_url(cfg.source_url)
    if not cfg.input_csv.exists():
        raise FileNotFoundError(f"Input file not found: {cfg.input_csv}")
    return read_contacts(cfg.input_csv)


def run_campaign(
    cfg: Config,
    contacts: list[dict[str, str]],
    *,
    on_log: Callable[[str], None] | None = None,
    on_preview: Callable[[str, Mapping[str, str], str, str, EmailMessage], None] | None = None,
    stop_event: Event | None = None,
    template_overrides: Mapping[str, str] | None = None,
) -> tuple[int, int]:
    already_sent = load_sent_addresses(cfg.sent_log_csv)
    subject_template = Template(cfg.subject_template_text or cfg.subject_template)
    body_template = Template(cfg.body_template_text) if cfg.body_template_text else load_template(cfg.body_template_path, DEFAULT_BODY)
    local_attachments, drive_attachments = split_attachments_by_size(
        cfg.attachment_paths,
        threshold_bytes=cfg.drive_upload_threshold_bytes,
    )
    uploaded_drive_links: list[DriveUploadResult] = []
    if drive_attachments:
        if cfg.dry_run:
            if on_log:
                on_log(
                    "Dry run: large attachments would be uploaded to Google Drive before sending."
                )
        else:
            if not cfg.drive_client_secrets_path:
                raise ValueError(
                    "Some attachments are larger than the Drive threshold, but Google Drive upload is not configured. "
                    "Set a Google OAuth client secrets JSON file and a token path, or lower the attachment size."
                )
            service, media_file_upload = _load_drive_service(cfg)
            for path in drive_attachments:
                if on_log:
                    on_log(f"Uploading to Google Drive: {path.name}")
                uploaded_drive_links.append(_upload_file_to_drive(service, media_file_upload, cfg, path))

    sent_count = 0
    skipped_count = 0

    if cfg.dry_run and on_log:
        on_log("Dry run enabled. No messages will be sent.")

    for row in contacts:
        if stop_event and stop_event.is_set():
            if on_log:
                on_log("Stopped by user.")
            break
        recipient = row.get("email", "").strip()
        if not recipient:
            skipped_count += 1
            continue
        if recipient.lower() in already_sent:
            skipped_count += 1
            continue

        context = merge_context(row, overrides=template_overrides)
        subject = render_text(subject_template, context)
        body = render_text(body_template, context)
        if uploaded_drive_links:
            body = f"{body}\n\n{_format_drive_link_section(uploaded_drive_links)}"
        elif drive_attachments and cfg.dry_run:
            body = f"{body}\n\nFiles uploaded to Google Drive:\n" + "\n".join(
                f"- {path.name}: (would upload during a live send)" for path in drive_attachments
            )
        message = make_message(cfg, recipient, subject, body, attachment_paths=tuple(str(path) for path in local_attachments))

        if on_preview:
            on_preview(recipient, context, subject, body, message)
        if on_log:
            on_log(f"{'[DRY] ' if cfg.dry_run else ''}Prepared: {recipient} | {context.get('station_name', '')}")

        if cfg.dry_run:
            if on_log:
                on_log(subject)
                on_log(body[:500].rstrip())
                on_log("-" * 60)
        else:
            send_message(cfg, message)
            append_sent_log(
                cfg.sent_log_csv,
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "email": recipient,
                    "station_name": context.get("station_name", ""),
                    "status": "sent",
                },
            )
            sent_count += 1
            already_sent.add(recipient.lower())
            if on_log:
                on_log(f"Sent to {recipient}")
            if cfg.delay_seconds > 0:
                if _sleep_with_stop(cfg.delay_seconds, stop_event):
                    if on_log:
                        on_log("Stopped by user.")
                    break

        if cfg.max_per_run is not None and sent_count >= cfg.max_per_run:
            if on_log:
                on_log(f"Reached max-per-run limit ({cfg.max_per_run}).")
            break

    if on_log:
        on_log(f"Done. Sent: {sent_count}, skipped already-sent: {skipped_count}")
    return sent_count, skipped_count


def main() -> int:
    cfg = parse_args()
    contacts = load_contacts(cfg)
    if cfg.max_per_run is not None:
        contacts = contacts[: cfg.max_per_run]
    run_campaign(cfg, contacts, on_log=print)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
