"""On-chain data skill — Etherscan API for balances, transactions, and gas prices."""

from __future__ import annotations

import json
import logging

import httpx

from ..base import Skill

log = logging.getLogger(__name__)

_ETHERSCAN_BASE = "https://api.etherscan.io/api"


class OnchainDataSkill(Skill):
    """Query Ethereum on-chain data via Etherscan (requires API key)."""

    def __init__(self, api_key: str = "") -> None:
        super().__init__()
        self._api_key = api_key
        self._call_count = 0
        self._success_count = 0

    # ── Metadata ──

    @property
    def name(self) -> str:
        return "onchain_data"

    @property
    def description(self) -> str:
        return "Query Ethereum balances, transaction history, and gas prices via Etherscan"

    @property
    def capabilities(self) -> list[str]:
        return ["onchain_data", "eth_balance", "tx_history", "gas_price"]

    @property
    def risk_level(self) -> str:
        return "review"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": True,
                "description": "'balance', 'txlist', or 'gas'",
            },
            "address": {
                "type": "str",
                "required": False,
                "description": "Ethereum address (required for balance/txlist)",
            },
            "count": {
                "type": "int",
                "required": False,
                "default": 5,
                "description": "Number of recent transactions to return (for txlist)",
            },
        }

    # ── Execute ──

    async def execute(self, params: dict) -> dict:
        self._call_count += 1

        if not self._api_key:
            return {
                "success": False,
                "result": "",
                "error": "Etherscan API key not configured. Pass api_key to constructor.",
            }

        action = params.get("action", "").lower()

        try:
            if action == "balance":
                return await self._balance(params)
            elif action == "txlist":
                return await self._txlist(params)
            elif action == "gas":
                return await self._gas()
            else:
                return {
                    "success": False,
                    "result": "",
                    "error": f"Unknown action '{action}'. Use: balance, txlist, gas.",
                }
        except httpx.TimeoutException:
            return {"success": False, "result": "", "error": "Etherscan request timed out"}
        except httpx.HTTPStatusError as e:
            return {
                "success": False,
                "result": "",
                "error": f"Etherscan HTTP {e.response.status_code}: {e.response.text[:300]}",
            }
        except Exception as e:
            log.warning("OnchainDataSkill error: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    async def _balance(self, params: dict) -> dict:
        address = params.get("address", "").strip()
        if not address:
            return {"success": False, "result": "", "error": "address is required for 'balance'"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                _ETHERSCAN_BASE,
                params={
                    "module": "account",
                    "action": "balance",
                    "address": address,
                    "tag": "latest",
                    "apikey": self._api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "1":
            return {
                "success": False,
                "result": "",
                "error": f"Etherscan error: {data.get('message', 'unknown')} — {data.get('result', '')}",
            }

        wei = int(data["result"])
        eth = wei / 1e18
        self._success_count += 1
        return {
            "success": True,
            "result": f"Address: {address}\nBalance: {eth:,.6f} ETH ({wei:,} wei)",
            "error": None,
        }

    async def _txlist(self, params: dict) -> dict:
        address = params.get("address", "").strip()
        count = params.get("count", 5)
        if not address:
            return {"success": False, "result": "", "error": "address is required for 'txlist'"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                _ETHERSCAN_BASE,
                params={
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": count,
                    "sort": "desc",
                    "apikey": self._api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "1":
            msg = data.get("message", "unknown")
            # "No transactions found" is status 0 but not really an error
            if "No transactions found" in msg:
                return {"success": True, "result": f"No transactions found for {address}", "error": None}
            return {
                "success": False,
                "result": "",
                "error": f"Etherscan error: {msg} — {data.get('result', '')}",
            }

        txs = data.get("result", [])[:count]
        if not txs:
            return {"success": True, "result": f"No transactions for {address}", "error": None}

        lines: list[str] = [f"Last {len(txs)} transactions for {address}:"]
        for tx in txs:
            value_eth = int(tx.get("value", "0")) / 1e18
            direction = "OUT" if tx.get("from", "").lower() == address.lower() else "IN"
            gas_used = tx.get("gasUsed", "?")
            status = "OK" if tx.get("isError", "0") == "0" else "FAIL"
            lines.append(
                f"  {direction} {value_eth:.4f} ETH | hash: {tx.get('hash', '?')[:16]}... "
                f"| block {tx.get('blockNumber', '?')} | gas {gas_used} | {status}"
            )

        self._success_count += 1
        return {"success": True, "result": "\n".join(lines), "error": None}

    async def _gas(self) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                _ETHERSCAN_BASE,
                params={
                    "module": "gastracker",
                    "action": "gasoracle",
                    "apikey": self._api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "1":
            return {
                "success": False,
                "result": "",
                "error": f"Etherscan error: {data.get('message', 'unknown')}",
            }

        result = data.get("result", {})
        safe_gas = result.get("SafeGasPrice", "?")
        propose_gas = result.get("ProposeGasPrice", "?")
        fast_gas = result.get("FastGasPrice", "?")
        base_fee = result.get("suggestBaseFee", "?")

        text = (
            f"Gas Prices (Gwei):\n"
            f"  Safe:     {safe_gas}\n"
            f"  Standard: {propose_gas}\n"
            f"  Fast:     {fast_gas}\n"
            f"  Base fee: {base_fee}"
        )
        self._success_count += 1
        return {"success": True, "result": text, "error": None}

    @property
    def usage_stats(self) -> dict:
        return {"call_count": self._call_count, "success_count": self._success_count}
