"""Patch selected YAML values while preserving all Markdown body bytes."""

import codecs
import json
import re
from dataclasses import dataclass

import yaml

from .md_converter import _decode_bytes


def _value_end(node):
    """Exclude trailing comments consumed by block collection end marks."""
    if isinstance(node, yaml.SequenceNode) and not node.flow_style and node.value:
        return _value_end(node.value[-1])
    if isinstance(node, yaml.MappingNode) and not node.flow_style and node.value:
        return _value_end(node.value[-1][1])
    return node.end_mark.index


@dataclass
class PropertiesDocument:
    raw: bytes
    text: str
    encoding: str
    bom: bytes
    start: int
    end: int
    body_start: int
    values: dict
    nodes: dict

    @classmethod
    def parse(cls, raw: bytes):
        if raw.startswith(codecs.BOM_UTF16_LE):
            bom, encoding = codecs.BOM_UTF16_LE, "utf-16-le"
        elif raw.startswith(codecs.BOM_UTF16_BE):
            bom, encoding = codecs.BOM_UTF16_BE, "utf-16-be"
        elif raw.startswith(codecs.BOM_UTF8):
            bom, encoding = codecs.BOM_UTF8, "utf-8"
        else:
            decoded = _decode_bytes(raw)
            if decoded is None:
                raise ValueError("無法辨識文件編碼。")
            bom, encoding = b"", decoded[1]
        text = raw[len(bom):].decode(encoding)
        if bom + text.encode(encoding) != raw:
            raise ValueError("文件編碼無法無損往返。")
        opening = re.match(r"---[ \t]*\r?\n", text)
        if not opening:
            return cls(raw, text, encoding, bom, 0, 0, 0, {}, {})
        closing = re.search(r"^---[ \t]*(?:\r?\n|$)", text[opening.end():], re.MULTILINE)
        if closing is None:
            raise ValueError("Front matter 缺少結束分隔線；請先在原始碼修正。")
        start = opening.end()
        end, body_start = start + closing.start(), start + closing.end()
        content = text[start:end]
        try:
            if any(isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken))
                   for token in yaml.scan(content)):
                raise ValueError("含 YAML 錨點或別名，請在原始碼編輯以保留參照。")
            node = yaml.compose(content, Loader=yaml.SafeLoader)
            values = yaml.safe_load(content)
            if node is None:
                values, nodes = {}, {}
            elif not isinstance(node, yaml.MappingNode) or node.flow_style:
                raise ValueError("屬性面板需要區塊式 YAML key/value 對照。")
            else:
                nodes = {}
                for key, value in node.value:
                    if key.tag != "tag:yaml.org,2002:str" or key.value in nodes:
                        raise ValueError("屬性名稱必須是唯一字串；請先修正重複或複合名稱。")
                    nodes[key.value] = value
        except yaml.YAMLError as exc:
            raise ValueError(f"YAML 格式不合法，未修改文件：{exc}") from exc
        return cls(raw, text, encoding, bom, start, end, body_start, values, nodes)

    @property
    def body_bytes(self):
        offset = len(self.bom) + len(self.text[:self.body_start].encode(self.encoding))
        return self.raw[offset:]

    def update(self, updates: dict) -> bytes:
        if not updates:
            return self.raw
        newline = "\r\n" if "\r\n" in self.text else "\n"
        content = self.text[self.start:self.end]
        edits, added = [], []
        for key, value in updates.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("屬性名稱不能是空白。")
            # JSON is a strict YAML subset and safely quotes multiline strings.
            try:
                serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "值請使用字串、數字、布林、清單或對照；日期字串請加引號。"
                ) from exc
            node = self.nodes.get(key)
            if node is None:
                added.append(json.dumps(key, ensure_ascii=False) + ": " + serialized + newline)
                continue
            begin, end = node.start_mark.index, _value_end(node)
            previous = content[begin:end]
            if begin and content[begin - 1] == ":":
                serialized = " " + serialized
            if previous.endswith("\n"):
                serialized += newline
            edits.append((begin, end, serialized))
        for begin, end, replacement in sorted(edits, reverse=True):
            content = content[:begin] + replacement + content[end:]
        if added:
            if content and not content.endswith("\n"):
                content += newline
            content += "".join(added)
        try:
            check = yaml.safe_load(content) or {}
        except yaml.YAMLError as exc:
            raise ValueError("修改後 YAML 無法解析，未儲存。") from exc
        if any(check.get(key) != value for key, value in updates.items()):
            raise ValueError("修改後屬性不符，未儲存。")
        if any(key not in check or check[key] != value
               for key, value in self.values.items() if key not in updates):
            raise ValueError("修改影響其他屬性，未儲存。")
        if self.body_start:
            header = self.text[:self.start] + content + self.text[self.end:self.body_start]
        else:
            header = "---" + newline + content + "---" + newline
        return self.bom + header.encode(self.encoding) + self.body_bytes
