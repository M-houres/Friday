"""Coordinator 的结果归一化与审批辅助函数。"""

from __future__ import annotations

from src.productization.result_protocol import normalize_result_payload


def normalize_final_result(final: dict, project_id: str = "", page_id: str = "") -> dict:
    if isinstance(final, dict) and final.get("normalized_result"):
        return dict(final["normalized_result"])
    if isinstance(final, dict) and isinstance(final.get("final_output"), dict):
        payload = final["final_output"].get("data", final["final_output"])
        return normalize_result_payload(
            payload if isinstance(payload, dict) else {"content": str(payload)},
            source=f"page:{project_id}/{page_id}" if page_id else "workflow",
        )
    if isinstance(final, dict):
        return normalize_result_payload(final, source="workflow")
    return normalize_result_payload({"content": str(final)}, source="workflow")


def extract_approvals(final: dict) -> list[dict]:
    if not isinstance(final, dict):
        return []
    final_output = final.get("final_output", {})
    payload = final_output.get("data", final_output) if isinstance(final_output, dict) else final_output
    if isinstance(payload, dict):
        approvals = payload.get("approvals", [])
        if isinstance(approvals, list):
            return [item for item in approvals if isinstance(item, dict) and item.get("id")]
    return []
