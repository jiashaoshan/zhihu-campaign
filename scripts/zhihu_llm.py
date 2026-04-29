#!/usr/bin/env python3
"""
LLM 调用模块 — 封装 DeepSeek API 调用
支持从环境变量/配置文件读取 API Key
"""
import json
import logging
import os
import requests
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# DeepSeek API 配置
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"  # V4系列，输出上限384K tokens
DEFAULT_TIMEOUT = 120

# 尝试从 ~/.openclaw/openclaw.json 读取 deepseek api key
_OPENCLAW_CONFIG_PATH = os.path.expanduser("~/.openclaw/openclaw.json")


def _read_deepseek_key() -> Optional[str]:
    """从多个来源读取 DeepSeek API Key"""
    # 1. 环境变量优先
    env_key = os.environ.get("DEEPSEEK_API_KEY")
    if env_key:
        return env_key

    # 2. 从 openclaw.json 的 env 中读取
    try:
        if os.path.exists(_OPENCLAW_CONFIG_PATH):
            with open(_OPENCLAW_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
            env = config.get("env", {})
            if isinstance(env, dict):
                for k, v in env.items():
                    if "deepseek" in k.lower() and "api" in k.lower() and "key" in k.lower():
                        if isinstance(v, str) and v.strip():
                            return v.strip()
                # 也试试直接叫 DEEPSEEK_API_KEY
                if "DEEPSEEK_API_KEY" in env:
                    return env["DEEPSEEK_API_KEY"]
    except Exception:
        pass

    return None


def get_api_key() -> str:
    """获取 DeepSeek API Key，如果没有则抛出异常"""
    key = _read_deepseek_key()
    if not key:
        raise EnvironmentError(
            "未找到 DEEPSEEK_API_KEY。请通过环境变量或 ~/.openclaw/openclaw.json 的 env 中配置。\n"
            "  export DEEPSEEK_API_KEY=sk-xxx\n"
            "  或在 openclaw.json 中添加: \"DEEPSEEK_API_KEY\": \"sk-xxx\""
        )
    return key


def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    response_format: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """
    调用 DeepSeek Chat API

    Args:
        system_prompt: 系统提示词
        user_prompt: 用户提示词
        model: 模型名称，默认 deepseek-chat
        temperature: 温度参数
        max_tokens: 最大生成 token 数
        response_format: 响应格式，如 {"type": "json_object"}
        timeout: 超时时间（秒）

    Returns:
        str: LLM 返回的文本内容

    Raises:
        requests.RequestException: API 调用失败
        ValueError: API 返回异常
    """
    api_key = get_api_key()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if response_format:
        payload["response_format"] = response_format

    logger.debug(f"LLM 请求: model={model}, system_prompt_len={len(system_prompt)}, user_prompt_len={len(user_prompt)}")

    try:
        resp = requests.post(
            DEEPSEEK_API_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()

        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()

        logger.debug(f"LLM 响应: {len(content)} 字符")
        return content

    except requests.exceptions.Timeout:
        logger.error(f"LLM 请求超时 (>{timeout}s)")
        raise
    except requests.exceptions.RequestException as e:
        logger.error(f"LLM 请求失败: {e}")
        if hasattr(e, "response") and e.response is not None:
            try:
                err_detail = e.response.json()
                logger.error(f"API 错误详情: {json.dumps(err_detail, ensure_ascii=False)}")
            except Exception:
                logger.error(f"API 原始响应: {e.response.text[:500]}")
        raise
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.error(f"解析 LLM 响应失败: {e}")
        raise ValueError(f"LLM 返回格式异常: {e}")


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """
    调用 LLM 并解析 JSON 响应
    注意：不使用 response_format 约束（避免截断输出），提示词已要求JSON格式

    Returns:
        dict: 解析后的 JSON 对象
    """
    content = call_llm(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )

    # 尝试解析 JSON
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # 尝试从文本中提取 JSON
        import re
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        logger.warning(f"LLM 返回非 JSON 格式，原始内容: {content[:200]}...")
        raise ValueError(f"LLM 返回不是有效的 JSON: {content[:100]}...")


if __name__ == "__main__":
    # 测试
    logging.basicConfig(level=logging.DEBUG)
    try:
        key = get_api_key()
        print(f"✓ API Key 找到: {key[:8]}...{key[-4:]}")
    except EnvironmentError as e:
        print(f"✗ {e}")
        exit(1)
