from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

HELP_TEXT = """📋 指令表

/mem       长期记忆：开关 / 查看 / 清理
/config    延迟、提示词、模型
/mode      人格风格（10 套，裸输入看列表）
/reset     清空当前会话上下文
/new       另开一条新对话
/stats     当前会话 token 用量
/stop      打断她正在生成的回复
/sid       查看会话 ID
/provider  查看 / 切换模型（管理员）
/name      设置会话显示名（管理员）
/cmd       本指令表"""


@register("astrbot_plugin_cmd_help", "hermes", "中文指令表 /cmd", "1.0.0")
class CmdHelp(Star):
    """提供一个中文指令参考页，补齐内置 /help 不列插件指令的空缺。"""

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.context = context

    @filter.command("cmd", alias={"指令", "指令表"})
    async def cmd_list(self, event: AstrMessageEvent):
        """查看全部指令及作用"""
        yield event.plain_result(HELP_TEXT)
