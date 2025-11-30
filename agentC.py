"""
Agent C - Hyperliquid PERP position reporter (API)
- Queries Hyperliquid info endpoint for clearinghouse/perp positions of a wallet
- Filters for target coins: HYPE, BTC, ETH, SOL, XRP
- Sends an email every run listing Size / Value / Mark / PNL (if available)
"""

import requests
import json
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Dict, List

# -----------------------------
# CONFIG - EDIT BEFORE USE
# -----------------------------
EMAIL_USER = "cryptosscalp@gmail.com"
EMAIL_PASS = "gfke olcu ulud zpnh"   # <-- replace with your app password or use secrets
ALERT_EMAIL = "25harshitgarg12345@gmail.com"

WALLET = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"   # your wallet (PERP account)
INFO_ENDPOINT = "https://api.hyperliquid.xyz/info"

TARGET_COINS = {"HYPE", "BTC", "ETH", "SOL", "XRP"}
# -----------------------------


def send_email(subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = EMAIL_USER
    msg["To"] = ALERT_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
        s.login(EMAIL_USER, EMAIL_PASS)
        s.send_message(msg)


def post_info(payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST to /info and return json (robust to errors)."""
    headers = {"Content-Type": "application/json"}
    resp = requests.post(INFO_ENDPOINT, headers=headers, json=payload, timeout=20)
    resp.raise_for_status()
    return resp.json()


def discover_position_lists(obj: Any) -> List[Any]:
    """
    Recursively search JSON for candidate position arrays where each element is
    a dict and contains keys suggesting a position (like 'size', 'asset', 'symbol', 'market').
    Returns list of candidate lists.
    """
    found = []

    if isinstance(obj, list):
        # is this list of dicts that look like positions?
        if obj and isinstance(obj[0], dict):
            sample = obj[0]
            # heuristics: presence of keys like 'size' and some name / asset / symbol /mark
            keys = set(sample.keys())
            if ("size" in keys or "positionSize" in keys or "qty" in keys or "notional" in keys) and (
                "asset" in keys or "symbol" in keys or "market" in keys or "token" in keys or "assetId" in keys
            ):
                found.append(obj)
        # also search children
        for item in obj:
            found.extend(discover_position_lists(item))
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(discover_position_lists(v))

    return found


def normalize_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    From a raw candidate dict, try to extract:
      - coin symbol (KEYS: 'symbol','asset','assetId','token','market')
      - size (size, positionSize, qty)
      - value / notional / positionValue
      - mark price (markPrice, mark)
      - pnl or unrealizedPnl or roePct
    Returns a normalized dict with string keys.
    """
    coin = None
    for k in ("symbol", "asset", "assetId", "token", "market", "name"):
        if k in item:
            coin = str(item[k])
            break

    # coin may have path like "BTC/USDT" -> take left part
    if coin and "/" in coin:
        coin = coin.split("/")[0]

    def get_number(*keys):
        for k in keys:
            if k in item and item[k] is not None:
                try:
                    return float(item[k])
                except Exception:
                    # sometimes numeric inside dict or str
                    try:
                        return float(str(item[k]).replace(",", ""))
                    except Exception:
                        pass
        return 0.0

    size = item.get("size") or item.get("positionSize") or item.get("qty") or item.get("amount") or 0
    # try cast to string for display
    try:
        size_display = str(size)
    except Exception:
        size_display = ""

    value = get_number("notional", "positionValue", "value", "usdValue", "notionalUsd")
    mark = get_number("markPrice", "mark", "price", "mark_price")
    pnl = get_number("unrealizedPnl", "pnl", "unrealized_pnl", "roe", "roePct", "roe_percent")

    return {
        "coin": (coin.upper() if coin else None),
        "size_display": size_display,
        "value": value,
        "mark": mark,
        "pnl": pnl,
        "raw": item,
    }


def extract_positions_from_info(info_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Use heuristics to find position lists in the info response and return
    normalized items.
    """
    positions = []
    # Common direct places
    if isinstance(info_json, dict):
        # some doc examples place clearinghouseState -> positions
        ch = info_json.get("clearinghouseState") or info_json.get("clearinghouse") or info_json.get("clearinghouse_state")
        if ch and isinstance(ch, dict) and "positions" in ch:
            raw_list = ch.get("positions") or []
            for it in raw_list:
                if isinstance(it, dict):
                    positions.append(normalize_item(it))

    # fallback: search the whole JSON for candidate lists
    cand_lists = discover_position_lists(info_json)
    for lst in cand_lists:
        for it in lst:
            if isinstance(it, dict):
                positions.append(normalize_item(it))

    # dedupe by coin name (keep first)
    seen = set()
    unique = []
    for p in positions:
        coin = p.get("coin")
        if not coin:
            continue
        if coin not in seen:
            seen.add(coin)
            unique.append(p)

    return unique


def main():
    # payload asks for clearinghouse/perp state of the user
    payload = {"type": "clearinghouseState", "user": WALLET}

    try:
        info = post_info(payload)
    except Exception as e:
        send_email("Agent C — Hyperliquid API error", f"Failed to fetch info endpoint:\n{e}")
        return

    positions = extract_positions_from_info(info)

    # filter for target coins
    found = [p for p in positions if p["coin"] in TARGET_COINS]

    if not found:
        send_email(
            "⚠ No Target Coin in Vault A",
            "None of your target coins (HYPE, BTC, ETH, SOL, XRP) are present in the account's perp positions."
        )
        return

    # Build email body
    body = "📌 TARGET PERP POSITIONS (HYPE, BTC, ETH, SOL, XRP)\n\n"
    for p in found:
        coin = p["coin"]
        size = p["size_display"]
        value = p["value"]
        mark = p["mark"]
        pnl = p["pnl"]
        body += f"{coin}\n  Size: {size}\n  Value (USD): ${value:,.2f}\n  Mark: {mark}\n  PNL (approx): {pnl}\n\n"

    body += f"Account (wallet): {WALLET}\n\nData source: {INFO_ENDPOINT}"

    send_email("📢 Hyperliquid Agent C — Target Perp Positions", body)


if __name__ == "__main__":
    main()
