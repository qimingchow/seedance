"""人脸素材审核（可插拔）。

默认策略：素材入库为 pending，进入「管理后台 → 素材审核」由管理员人工批准/驳回。
对肖像权合规而言，这是稳妥做法（有人确认本人授权）。

若你确认了火山的内容安全 / 肖像授权接口，在 submit_for_review 内调用它，
并据返回把状态置为 'approved' / 'rejected' / 'pending'。例如：

    def submit_for_review(asset_meta):
        result = call_volcengine_content_moderation(asset_meta["tos_url"])
        return "approved" if result.is_safe else "rejected"
"""
from __future__ import annotations


def submit_for_review(asset_meta: dict) -> str:
    """提交审核，返回初始审核状态。默认进入人工审核队列。"""
    # TODO: 接入火山内容安全 / 肖像授权 API 后，在此返回审核结论
    return "pending"
