import hashlib
import hmac
import logging
import math
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from flask import jsonify
import requests

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("vortex_backend")

VEXPAY_BASE_URL = os.getenv("VEXPAY_BASE_URL", "https://api.pay.vexwallet.co").rstrip("/")
VEXPAY_PLATFORM_ID = os.getenv("VEXPAY_PLATFORM_ID", "plt_2d70c85527aa47e3a97b55ac430ab415").strip()
DB_PATH = os.getenv("VORTEX_DB_PATH", "/tmp/vortex_backend.db")
VENEZUELA_TZ = timezone(timedelta(hours=-4))

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
VE_PHONE_CORE_REGEX = re.compile(r"^(412|414|424|416|426)\d{7}$")
DOC_NUMBER_REGEX = re.compile(r"^\d{5,10}$")
BANK_REF_REGEX = re.compile(r"^\d{6,12}$")
OTP_BANK_REGEX = re.compile(r"^\d{6,8}$")
VALID_DOC_TYPES = {"V", "E", "J", "P", "G"}
APPROVED_STATUSES = {"COMPLETED", "DONE", "APPROVED", "SUCCESS", "VERIFIED"}
REJECTED_STATUSES = {"FAILED", "REJECTED", "CANCELED", "CANCELLED", "EXPIRED", "ERROR", "PENDING"}


def _get_conn():
    parent_dir = os.path.dirname(DB_PATH)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    # AG2-A10: busy_timeout SIEMPRE antes de cualquier operacion/PRAGMA
    conn.execute("PRAGMA busy_timeout=10000;")
    return conn


def init_db():
    conn = _get_conn()
    try:
        # AG2-A10: WAL se activa una sola vez en inicializacion despues de busy_timeout
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS auth_otps (
                email TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rate_limits (
                bucket_key TEXT PRIMARY KEY,
                hits INTEGER NOT NULL,
                reset_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS vexpay_webhooks (
                external_ref TEXT PRIMARY KEY,
                game TEXT NOT NULL,
                event_type TEXT,
                status TEXT NOT NULL,
                payment_id TEXT,
                bank_reference TEXT,
                method TEXT,
                usd_amount REAL,
                ves_amount REAL,
                reason TEXT,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS consumed_bank_references (
                normalized_ref TEXT PRIMARY KEY,
                raw_reference TEXT NOT NULL,
                suffix6 TEXT NOT NULL DEFAULT '',
                game TEXT NOT NULL,
                external_ref TEXT NOT NULL,
                method TEXT NOT NULL,
                amount_usd REAL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inflight_bank_references (
                lock_key TEXT PRIMARY KEY,
                external_ref TEXT NOT NULL,
                game TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kv_cache (
                cache_key TEXT PRIMARY KEY,
                cache_value TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
        """)
        # Migracion segura para tablas previas sin columna suffix6
        try:
            conn.execute("ALTER TABLE consumed_bank_references ADD COLUMN suffix6 TEXT NOT NULL DEFAULT '';")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_consumed_suffix6 ON consumed_bank_references(suffix6);"
        )
        conn.commit()
    finally:
        conn.close()


init_db()


def get_venezuela_time_payload():
    now_utc = datetime.now(timezone.utc)
    now_ve = now_utc.astimezone(VENEZUELA_TZ)
    return {
        "status": "ok",
        "utcEpochMs": int(now_utc.timestamp() * 1000),
        "isoUtc": now_utc.isoformat(),
        "venezuelaDate": now_ve.strftime("%Y-%m-%d"),
        "venezuelaTime24": now_ve.strftime("%H:%M:%S"),
        "utcOffset": "-04:00",
        "timezone": "America/Caracas"
    }


def mask_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    visible = local[:2] if len(local) >= 2 else local[:1]
    return f"{visible}***@{domain}"


def get_client_ip(req) -> str:
    """AG2-A10: Usa la IP mas a la derecha anadida por el proxy de Render (inmune a spoofing en [0])."""
    forwarded = req.headers.get("X-Forwarded-For", "").strip()
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-1][:64]
    return (req.remote_addr or "unknown")[:64]


def check_rate_limit(bucket_key: str, max_hits: int, window_seconds: int):
    now = int(time.time())
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT hits, reset_at FROM rate_limits WHERE bucket_key = ?",
            (bucket_key,)
        ).fetchone()

        if not row or now >= row["reset_at"]:
            reset_at = now + window_seconds
            conn.execute(
                "INSERT OR REPLACE INTO rate_limits (bucket_key, hits, reset_at) VALUES (?, ?, ?)",
                (bucket_key, 1, reset_at)
            )
            conn.commit()
            return True, 0

        if row["hits"] >= max_hits:
            conn.commit()
            return False, max(1, row["reset_at"] - now)

        conn.execute(
            "UPDATE rate_limits SET hits = hits + 1 WHERE bucket_key = ?",
            (bucket_key,)
        )
        conn.commit()
        return True, 0
    finally:
        conn.close()


def is_valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(EMAIL_REGEX.match(email))


def normalize_ve_phone(raw_phone) -> str:
    digits = "".join(filter(str.isdigit, str(raw_phone or "").strip()))
    core = re.sub(r"^58", "", digits).lstrip("0")
    if not VE_PHONE_CORE_REGEX.match(core):
        return ""
    return f"58{core}"


def normalize_doc_id(doc_type, doc_number) -> str:
    t = str(doc_type or "V").strip().upper()
    n = "".join(filter(str.isdigit, str(doc_number or "").strip()))
    if t not in VALID_DOC_TYPES or not DOC_NUMBER_REGEX.match(n):
        return ""
    return f"{t}{n}"


def parse_positive_float(val, min_val=0.01, max_val=100000000.0):
    if val is None or isinstance(val, bool):
        return None
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    if math.isnan(num) or math.isinf(num) or num < min_val or num > max_val:
        return None
    return round(num, 2)


def parse_bank_code(val):
    if val is None or isinstance(val, bool):
        return None
    raw = str(val).strip()
    if not raw.isdigit():
        return None
    code = int(raw)
    if code < 100 or code > 999:
        return None
    return code


def normalize_bank_reference(raw_ref):
    """Retorna (raw_digits, canonical_sin_ceros, suffix6_preservando_ceros)."""
    digits = "".join(filter(str.isdigit, str(raw_ref or "").strip()))
    if not BANK_REF_REGEX.match(digits):
        return None, None, None
    canonical = digits.lstrip("0") or "0"
    suffix6 = digits[-6:] if len(digits) >= 6 else ""
    return digits, canonical, suffix6


def extract_bank_reference_from_response(body):
    """Extrae referencia bancaria desde raiz, data o payload cubriendo todas las variantes de VexPay."""
    if not isinstance(body, dict):
        return ""
    candidates = []
    for container_key in ("data", "payload", "payment"):
        sub = body.get(container_key)
        if isinstance(sub, dict):
            candidates.append(sub)
    candidates.append(body)
    for src in candidates:
        for key in ("bankReference", "bank_reference", "reference", "paymentReference", "refNumber"):
            val = src.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
    return ""


def validate_bank_otp(raw_token) -> str:
    token = "".join(filter(str.isdigit, str(raw_token or "").strip()))
    if not OTP_BANK_REGEX.match(token):
        return ""
    return token


def parse_vexpay_response(response):
    """Parsea respuesta JSON de VexPay sin filtrar HTML crudo ni devolver HTTP 200 ante errores."""
    try:
        data = response.json()
        if not isinstance(data, (dict, list)):
            raise ValueError("JSON no estructurado")
        return jsonify(data), response.status_code
    except ValueError:
        safe_code = response.status_code if response.status_code >= 400 else 502
        logger.warning("Respuesta no-JSON recibida desde VexPay (HTTP %s)", response.status_code)
        return jsonify({
            "status": "error",
            "message": "Respuesta inválida o temporalmente no disponible de la pasarela bancaria"
        }), safe_code


def is_vexpay_approved(data, status_code: int) -> bool:
    if status_code < 200 or status_code >= 300 or not isinstance(data, dict):
        return False
    nested = data.get("data") or data.get("payload") or {}
    if not isinstance(nested, dict):
        nested = {}
    for flag_key in ("ok", "success", "verified", "approved"):
        if data.get(flag_key) is False or nested.get(flag_key) is False:
            return False
    raw_status = str(nested.get("status") or data.get("status") or "").strip().upper()
    if raw_status:
        if raw_status in REJECTED_STATUSES:
            return False
        return raw_status in APPROVED_STATUSES
    return any(
        data.get(k) is True or nested.get(k) is True
        for k in ("verified", "approved", "success", "ok")
    )


def get_cached_vexpay_rate(api_key: str):
    now = int(time.time())
    stale_fallback = None
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT cache_value, expires_at FROM kv_cache WHERE cache_key = 'vexpay_usd_rate'"
        ).fetchone()
        if row and row["expires_at"] > now:
            return float(row["cache_value"])
        row_last = conn.execute(
            "SELECT cache_value, expires_at FROM kv_cache WHERE cache_key = 'vexpay_usd_rate_last_known'"
        ).fetchone()
        if row_last and row_last["expires_at"] > now:
            stale_fallback = float(row_last["cache_value"])
    finally:
        conn.close()

    if api_key:
        try:
            resp = requests.get(
                f"{VEXPAY_BASE_URL}/v1/quote?usdAmount=1",
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                timeout=15
            )
            if resp.status_code == 200:
                body = resp.json()
                nested = body.get("data") if isinstance(body.get("data"), dict) else body
                rate = nested.get("rate") or nested.get("vesAmount") or nested.get("bcvRate")
                parsed_rate = parse_positive_float(rate, min_val=1.0)
                if parsed_rate:
                    conn = _get_conn()
                    try:
                        conn.execute(
                            "INSERT OR REPLACE INTO kv_cache (cache_key, cache_value, expires_at) VALUES (?, ?, ?)",
                            ("vexpay_usd_rate", str(parsed_rate), now + 600)
                        )
                        conn.execute(
                            "INSERT OR REPLACE INTO kv_cache (cache_key, cache_value, expires_at) VALUES (?, ?, ?)",
                            ("vexpay_usd_rate_last_known", str(parsed_rate), now + 86400)
                        )
                        conn.commit()
                    finally:
                        conn.close()
                    return parsed_rate
        except Exception:
            logger.warning("No se pudo refrescar tasa cacheada de VexPay; usando respaldo")

    if stale_fallback is not None:
        return stale_fallback

    env_fallback = parse_positive_float(os.getenv("MIN_VES_FALLBACK_RATE"), min_val=1.0)
    return env_fallback


def validate_min_ves_for_one_usd(ves_amount: float, api_key: str):
    """AG2-B6: Validacion fail-closed del minimo $1.00 USD en Bs."""
    rate = get_cached_vexpay_rate(api_key)
    if rate is None:
        return False, "No se pudo verificar la tasa oficial mínima en este momento. Intenta en unos segundos.", 503
    min_ves = round(rate * 0.97, 2)
    if ves_amount < min_ves:
        return False, f"El monto mínimo bancario es $1.00 USD (aprox. Bs. {rate:.2f}).", 400
    return True, None, 200


def reserve_bank_reference_inflight(raw_ref: str, canonical_ref: str, suffix6: str,
                                    game: str, external_ref: str, ttl_seconds: int = 60):
    """Reserva atomica pre-VexPay (BEGIN IMMEDIATE) para cerrar carreras TOCTOU + sufijo-6."""
    now = int(time.time())
    lock_key = suffix6 or canonical_ref
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM inflight_bank_references WHERE expires_at <= ?", (now,))

        if suffix6:
            consumed = conn.execute(
                """
                SELECT external_ref, game FROM consumed_bank_references
                WHERE normalized_ref = ? OR (suffix6 != '' AND suffix6 = ?)
                LIMIT 1
                """,
                (canonical_ref, suffix6)
            ).fetchone()
        else:
            consumed = conn.execute(
                "SELECT external_ref, game FROM consumed_bank_references WHERE normalized_ref = ? LIMIT 1",
                (canonical_ref,)
            ).fetchone()

        if consumed and (not external_ref or consumed["external_ref"] != external_ref):
            conn.commit()
            return False, "consumed", dict(consumed)

        inflight = conn.execute(
            "SELECT external_ref, game FROM inflight_bank_references WHERE lock_key = ? LIMIT 1",
            (lock_key,)
        ).fetchone()
        if inflight and (not external_ref or inflight["external_ref"] != external_ref):
            conn.commit()
            return False, "inflight", dict(inflight)

        conn.execute(
            """
            INSERT OR REPLACE INTO inflight_bank_references (lock_key, external_ref, game, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (lock_key, str(external_ref or ""), game, now + ttl_seconds)
        )
        conn.commit()
        return True, "reserved", None
    finally:
        conn.close()


def release_bank_reference_inflight(canonical_ref: str, suffix6: str, external_ref: str):
    lock_key = suffix6 or canonical_ref
    if not lock_key:
        return
    conn = _get_conn()
    try:
        conn.execute(
            "DELETE FROM inflight_bank_references WHERE lock_key = ? AND external_ref = ?",
            (lock_key, str(external_ref or ""))
        )
        conn.commit()
    finally:
        conn.close()


def record_consumed_bank_reference(raw_ref: str, canonical_ref: str, suffix6: str,
                                   game: str, external_ref: str, method: str, amount_usd=None):
    if not canonical_ref:
        return
    lock_key = suffix6 or canonical_ref
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT OR IGNORE INTO consumed_bank_references
            (normalized_ref, raw_reference, suffix6, game, external_ref, method, amount_usd, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (canonical_ref, str(raw_ref), str(suffix6 or ""), game, str(external_ref or ""), method, amount_usd, int(time.time()))
        )
        conn.execute(
            "DELETE FROM inflight_bank_references WHERE lock_key = ?",
            (lock_key,)
        )
        conn.commit()
    finally:
        conn.close()


def save_webhook_event(external_ref: str, game: str, event_type: str, status: str,
                       payment_id=None, bank_reference=None, method=None,
                       usd_amount=None, ves_amount=None, reason=None):
    """AG2-C8: Nunca degrada un estado COMPLETED/APPROVED ya persistido."""
    if not external_ref:
        return
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO vexpay_webhooks
            (external_ref, game, event_type, status, payment_id, bank_reference, method, usd_amount, ves_amount, reason, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(external_ref) DO UPDATE SET
                game = excluded.game,
                event_type = excluded.event_type,
                status = excluded.status,
                payment_id = COALESCE(NULLIF(excluded.payment_id, ''), vexpay_webhooks.payment_id),
                bank_reference = COALESCE(NULLIF(excluded.bank_reference, ''), vexpay_webhooks.bank_reference),
                method = COALESCE(NULLIF(excluded.method, ''), vexpay_webhooks.method),
                usd_amount = COALESCE(excluded.usd_amount, vexpay_webhooks.usd_amount),
                ves_amount = COALESCE(excluded.ves_amount, vexpay_webhooks.ves_amount),
                reason = excluded.reason,
                updated_at = excluded.updated_at
            WHERE vexpay_webhooks.status NOT IN ('COMPLETED', 'DONE', 'APPROVED', 'SUCCESS', 'VERIFIED')
            """,
            (
                str(external_ref).strip(),
                game,
                str(event_type or ""),
                str(status or "UNKNOWN").upper(),
                str(payment_id or ""),
                str(bank_reference or ""),
                str(method or ""),
                usd_amount,
                ves_amount,
                str(reason or ""),
                int(time.time())
            )
        )
        conn.commit()
    finally:
        conn.close()


def get_webhook_status(external_ref: str, expected_game: str = None):
    conn = _get_conn()
    try:
        if expected_game:
            row = conn.execute(
                """
                SELECT external_ref, game, status, bank_reference, method, updated_at
                FROM vexpay_webhooks
                WHERE external_ref = ? AND game = ?
                """,
                (external_ref.strip(), expected_game)
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT external_ref, game, status, bank_reference, method, updated_at
                FROM vexpay_webhooks
                WHERE external_ref = ?
                """,
                (external_ref.strip(),)
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# --- GESTION DE OTP DE CORREO EN BD (MULTI-WORKER + TTL + MAX. 5 INTENTOS) ---
OTP_TTL_SECONDS = 600
OTP_MAX_ATTEMPTS = 5


def generate_secure_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_otp(email: str, code: str, salt: str) -> str:
    secret_pepper = os.getenv("OTP_HMAC_SECRET") or os.getenv("VEXPAY_API_KEY") or "vortex-otp-pepper"
    msg = f"{email}:{code}:{salt}".encode("utf-8")
    return hmac.new(secret_pepper.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def store_email_otp(email: str, code: str):
    now = int(time.time())
    salt = secrets.token_hex(16)
    code_hash = _hash_otp(email, code, salt)
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO auth_otps (email, code_hash, salt, expires_at, attempts, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (email, code_hash, salt, now + OTP_TTL_SECONDS, now)
        )
        conn.commit()
    finally:
        conn.close()


def verify_email_otp(email: str, user_code: str):
    now = int(time.time())
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT code_hash, salt, expires_at, attempts FROM auth_otps WHERE email = ?",
            (email,)
        ).fetchone()

        if not row:
            conn.commit()
            return False, "Código incorrecto o expirado"

        if now > row["expires_at"]:
            conn.execute("DELETE FROM auth_otps WHERE email = ?", (email,))
            conn.commit()
            return False, "El código ha expirado. Solicita uno nuevo."

        if row["attempts"] >= OTP_MAX_ATTEMPTS:
            conn.execute("DELETE FROM auth_otps WHERE email = ?", (email,))
            conn.commit()
            return False, "Demasiados intentos fallidos. Solicita un nuevo código."

        candidate_hash = _hash_otp(email, user_code, row["salt"])
        if not hmac.compare_digest(candidate_hash, row["code_hash"]):
            new_attempts = row["attempts"] + 1
            if new_attempts >= OTP_MAX_ATTEMPTS:
                conn.execute("DELETE FROM auth_otps WHERE email = ?", (email,))
                conn.commit()
                return False, "Código incorrecto. Se agotaron los intentos; solicita uno nuevo."
            conn.execute(
                "UPDATE auth_otps SET attempts = ? WHERE email = ?",
                (new_attempts, email)
            )
            conn.commit()
            remaining = OTP_MAX_ATTEMPTS - new_attempts
            return False, f"Código incorrecto. Te quedan {remaining} intento(s)."

        conn.execute("DELETE FROM auth_otps WHERE email = ?", (email,))
        conn.commit()
        return True, "Correo verificado exitosamente"
    finally:
        conn.close()
