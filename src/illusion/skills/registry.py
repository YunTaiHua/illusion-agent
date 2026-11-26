"""
技能和资源注册表。

技能和斜杠命令共享 ``SkillDefinition`` 数据模型，但使用独立的集合。
这样同名技能和命令都能保留，技能列表也不会把命令计入其中。
"""

from __future__ import annotations

from illusion.skills.types import SkillDefinition


class SkillRegistry:
    """按名称保存真技能和斜杠命令。"""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        self._commands: dict[str, SkillDefinition] = {}

    @staticmethod
    def _key(name: str) -> str:
        return name.casefold()

    @classmethod
    def _find_key(cls, entries: dict[str, SkillDefinition], name: str) -> str | None:
        if name in entries:
            return name
        wanted = cls._key(name)
        for key in entries:
            if cls._key(key) == wanted:
                return key
        return None

    @classmethod
    def _put(
        cls,
        entries: dict[str, SkillDefinition],
        definition: SkillDefinition,
        *,
        replace: bool,
    ) -> bool:
        existing_key = cls._find_key(entries, definition.name)
        if existing_key is not None:
            if not replace:
                return False
            del entries[existing_key]
        entries[definition.name] = definition
        return True

    def register(self, definition: SkillDefinition) -> None:
        """注册一个定义。

        真技能按现有加载顺序覆盖同名定义，项目技能可以覆盖用户技能。
        命令按首次发现的优先级保留，用户命令因此可以覆盖项目命令而不被
        后续扫描覆盖。
        """
        if definition.is_command:
            self.register_command(definition)
        else:
            self._put(self._skills, definition, replace=True)

    def register_command(self, command: SkillDefinition, *, replace: bool = False) -> bool:
        """注册一个命令并返回是否写入。

        ``replace=False`` 保证同名命令不会被后续来源悄悄替换。
        """
        if not command.is_command:
            raise ValueError("register_command expects a command definition")
        return self._put(self._commands, command, replace=replace)

    def get(self, name: str) -> SkillDefinition | None:
        """按名称返回真技能，不检索斜杠命令。"""
        key = self._find_key(self._skills, name)
        return self._skills.get(key) if key is not None else None

    def get_command(self, name: str) -> SkillDefinition | None:
        """按名称返回斜杠命令，不检索真技能。"""
        key = self._find_key(self._commands, name)
        return self._commands.get(key) if key is not None else None

    def get_any(self, name: str) -> SkillDefinition | None:
        """按名称返回一个资源，命令优先于同名技能。"""
        return self.get_command(name) or self.get(name)

    def list_skills(self) -> list[SkillDefinition]:
        """返回所有真技能，按名称排序。"""
        return sorted(self._skills.values(), key=lambda skill: skill.name.casefold())

    def list_commands(self) -> list[SkillDefinition]:
        """返回所有斜杠命令，按名称排序。"""
        return sorted(self._commands.values(), key=lambda command: command.name.casefold())

    def list_entries(self) -> list[SkillDefinition]:
        """返回技能和命令的合并视图，供斜杠发现使用。"""
        return sorted(
            [*self._skills.values(), *self._commands.values()],
            key=lambda definition: (definition.name.casefold(), definition.kind),
        )
