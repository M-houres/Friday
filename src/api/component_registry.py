"""工具 → UI组件绑定 —— 每个工具自动对应一个前端组件"""

import json
import logging
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class ComponentBinding:
    tool_name: str
    component_id: str             # 前端组件标识，如 "StyleSelector" "GenerationProgress"
    display_name: str = ""
    description: str = ""
    props: dict = field(default_factory=dict)  # 默认属性
    loading_message: str = "处理中..."
    success_message: str = "完成"
    render_mode: str = "inline"   # inline | modal | sidebar | fullscreen
    icon: str = ""
    min_width: str = ""
    max_height: str = ""


class ComponentRegistry:
    """工具→组件绑定注册中心"""

    def __init__(self):
        self._bindings: dict[str, ComponentBinding] = {}
        self._custom_factories: dict[str, Callable[[dict], dict]] = {}

    def bind(self, tool_name: str, component_id: str, **kwargs):
        """绑定工具到前端组件"""
        binding = ComponentBinding(
            tool_name=tool_name,
            component_id=component_id,
            display_name=kwargs.get("display_name", tool_name),
            description=kwargs.get("description", ""),
            props=kwargs.get("props", {}),
            loading_message=kwargs.get("loading_message", "处理中..."),
            success_message=kwargs.get("success_message", "完成"),
            render_mode=kwargs.get("render_mode", "inline"),
            icon=kwargs.get("icon", ""),
            min_width=kwargs.get("min_width", ""),
            max_height=kwargs.get("max_height", ""),
        )
        self._bindings[tool_name] = binding
        logger.info(f"Component binding: {tool_name} → {component_id}")

    def unbind(self, tool_name: str):
        self._bindings.pop(tool_name, None)

    def get_component(self, tool_name: str) -> dict | None:
        binding = self._bindings.get(tool_name)
        if binding is None:
            return None
        return {
            "tool": binding.tool_name,
            "component": binding.component_id,
            "display_name": binding.display_name,
            "description": binding.description,
            "props": binding.props,
            "loading_message": binding.loading_message,
            "success_message": binding.success_message,
            "render_mode": binding.render_mode,
            "icon": binding.icon,
            "min_width": binding.min_width,
            "max_height": binding.max_height,
        }

    def get_all_bindings(self) -> list[dict]:
        return [self.get_component(name) for name in self._bindings]

    def get_manifest(self) -> dict:
        """生成前端清单"""
        components = {}
        for name, binding in self._bindings.items():
            components[binding.tool_name] = {
                "component": binding.component_id,
                "props": binding.props,
                "loading": binding.loading_message,
                "success": binding.success_message,
                "mode": binding.render_mode,
            }
        return {
            "version": "1.0",
            "components": components,
            "defaultComponents": {
                "text": "FridayTextDisplay",
                "json": "FridayJsonViewer",
                "image": "FridayImageViewer",
                "video": "FridayVideoPlayer",
                "audio": "FridayAudioPlayer",
                "file": "FridayFileDownload",
                "progress": "FridayProgressBar",
                "approval": "FridayApprovalCard",
                "error": "FridayErrorDisplay",
            },
        }

    def register_factory(self, tool_name: str, factory: Callable[[dict], dict]):
        """注册动态组件工厂（根据运行时数据决定渲染什么）"""
        self._custom_factories[tool_name] = factory

    def get_dynamic_component(self, tool_name: str, output: dict) -> dict | None:
        factory = self._custom_factories.get(tool_name)
        if factory is None:
            return None
        return factory(output)


component_registry = ComponentRegistry()


# ── 预设默认绑定 ──

def setup_default_bindings():
    """设置默认的工具→组件绑定"""
    defaults = [
        ("web_search", "FridaySearchResults", "mag", "搜索结果"),
        ("fetch_url", "FridayWebPreview", "globe", "网页预览"),
        ("read_file", "FridayCodeViewer", "file-code", "文件内容"),
        ("write_file", "FridayFileAction", "save", "文件已保存"),
        ("generate_image", "FridayImageViewer", "image", "图片生成"),
        ("generate_tts", "FridayAudioPlayer", "volume-2", "语音生成"),
        ("generate_video", "FridayVideoPlayer", "video", "视频生成"),
        ("summarize_text", "FridayTextDisplay", "file-text", "摘要"),
        ("extract_json", "FridayJsonViewer", "braces", "数据提取"),
        ("run_python", "FridayCodeOutput", "terminal", "代码执行"),
    ]
    for tool_name, component_id, icon, description in defaults:
        component_registry.bind(
            tool_name=tool_name,
            component_id=component_id,
            icon=icon,
            description=description,
        )


setup_default_bindings()
