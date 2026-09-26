"""Read-only wording for saved model evidence, independent of Qt and transport."""
from __future__ import annotations


def capability_summary(profile, *, matches_saved: bool, vision_enabled: bool) -> str:
    if profile is None:
        text_state = "尚未保存配置。"
    elif not matches_saved:
        text_state = "表单有未保存修改；旧测试不适用于当前填写。"
    else:
        result = profile.last_connection_test
        if result is None:
            text_state = "已保存，尚无短文本测试结果。"
        elif result.status == "succeeded":
            text_state = "已保存配置曾通过短文本测试（不是实时状态）。"
        elif result.status == "stale":
            text_state = "旧测试已失效，请针对当前配置重新测试。"
        else:
            text_state = "上次短文本测试未成功；可核对配置后手动测试。"
    image_state = ("允许在逐次确认后发送图片；这不是识图测试通过。" if vision_enabled
                   else "图片发送未允许；文字备课和本地资料仍可使用。")
    return ("文字连接：" + text_state + "\n图片任务：" + image_state +
            "\n化学图识读、题目切分与教学内容质量：本页短文本测试不验证这些能力。")
