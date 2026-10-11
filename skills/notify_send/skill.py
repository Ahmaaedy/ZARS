import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from zars_sdk import build_parser, manifest_for
from core.skill_utils import resolve_path, safe_emit


def _load_telegram_config():
    root = Path(__file__).resolve().parent.parent.parent
    config_path = root / "config" / "telegram.json"
    if not config_path.exists():
        return None, f"telegram config not found: {config_path}"
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return None, f"cannot read telegram config: {e}"
    required = ["api_id", "api_hash"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        return None, f"telegram config missing: {', '.join(missing)}"
    return cfg, None


def _send(cfg, message=None, file_path=None, caption=None):
    from telethon import TelegramClient

    async def _run():
        client = TelegramClient(
            cfg.get("session_name", "zars_notify"),
            cfg["api_id"],
            cfg["api_hash"],
        )
        try:
            await client.start()
            target = cfg.get("target_chat", "me")
            if file_path:
                await client.send_file(target, file_path, caption=caption or "")
            else:
                await client.send_message(target, message)
        finally:
            await client.disconnect()

    asyncio.run(_run())


def main():
    ns = build_parser(manifest_for(__file__)).parse_args()
    message = ns.message
    file_path = ns.file_path
    caption = ns.caption

    if not message and not file_path:
        safe_emit(False, error="provide either --message or --file_path")
    if message and file_path:
        safe_emit(False, error="provide only one of --message or --file_path, not both")
    if message and not message.strip():
        safe_emit(False, error="message cannot be empty or whitespace")

    resolved = None
    if file_path:
        try:
            resolved = resolve_path(file_path)
        except ValueError as e:
            safe_emit(False, error=str(e))
        if not resolved.exists():
            safe_emit(False, error=f"file not found: {resolved}")
        size_mb = resolved.stat().st_size / (1024 * 1024)
        if size_mb > 2000:
            safe_emit(False, error=f"file too large for Telegram: {size_mb:.0f}MB (limit ~2GB)")

    cfg, err = _load_telegram_config()
    if cfg is None:
        safe_emit(False, error=err)

    try:
        if resolved:
            _send(cfg, file_path=str(resolved), caption=caption)
            safe_emit(True, {"action": "send_file", "file": str(resolved), "target": cfg.get("target_chat", "me")}, display_text=f"file sent: {resolved.name}")
        else:
            _send(cfg, message=message)
            safe_emit(True, {"action": "send_text", "target": cfg.get("target_chat", "me")}, display_text="message sent")
    except Exception as e:
        err_str = str(e)
        if "flood" in err_str.lower() or "wait" in err_str.lower():
            safe_emit(False, error=f"rate limited: {err_str}", status="rate_limited")
        elif "auth" in err_str.lower() or "session" in err_str.lower():
            safe_emit(False, error=f"auth error — run initial login manually: {err_str}", status="auth_error")
        else:
            safe_emit(False, error=f"send failed: {err_str}")


if __name__ == "__main__":
    main()
