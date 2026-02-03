"""
Kiyomi Lite — AI Provider Interface
Unified interface to Gemini, Claude, GPT, and CLI tools.
Handles all the complexity so the bot.py doesn't have to.
"""
import asyncio
import json
import logging

logger = logging.getLogger(__name__)


async def chat(
    message: str,
    provider: str,
    model: str,
    api_key: str,
    system_prompt: str = "",
    history: list = None,
    tools_enabled: bool = True,
    cli_path: str = "",
    cli_timeout: int = 60,
) -> str:
    """Send a message to an AI provider and get a response.
    
    This is the ONLY function the bot needs to call.
    All provider-specific logic is hidden here.
    Supports both API and CLI-based providers.
    """
    if not message or not message.strip():
        return "I didn't catch that — could you say that again? 😊"
    
    # Check if this is a CLI provider
    if provider.endswith("-cli"):
        return await _chat_cli(message, provider, system_prompt, history, cli_path, cli_timeout)
    
    # API providers require API key
    if not api_key:
        return "I'm not connected to an AI service yet. Please set up my connection in Settings."
    
    try:
        if provider == "gemini":
            return await _chat_gemini(message, model, api_key, system_prompt, history, tools_enabled)
        elif provider == "anthropic":
            return await _chat_anthropic(message, model, api_key, system_prompt, history, tools_enabled)
        elif provider == "openai":
            return await _chat_openai(message, model, api_key, system_prompt, history, tools_enabled)
        else:
            return await _chat_gemini(message, model, api_key, system_prompt, history, tools_enabled)
    except Exception as e:
        logger.error(f"AI chat error ({provider}): {e}")
        return f"Sorry, I had trouble connecting to my AI brain. Error: {str(e)[:100]}"


async def _chat_cli(
    message: str,
    provider: str,
    system_prompt: str = "",
    history: list = None,
    cli_path: str = "",
    cli_timeout: int = 60,
) -> str:
    """Chat via CLI tools (Claude Code CLI, Codex CLI, etc.)."""
    try:
        from cli_router import CLIRouter
        
        router = CLIRouter(timeout=cli_timeout)
        
        # Combine system prompt and message for CLI
        full_prompt = message
        if system_prompt:
            full_prompt = f"{system_prompt}\n\nUser: {message}"
        
        # Add recent history context for CLI (last 4 messages)
        if history:
            recent_history = history[-4:]  # Last 2 conversations
            history_text = ""
            for msg in recent_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                history_text += f"{role.title()}: {content}\n"
            
            if history_text:
                full_prompt = f"Recent conversation:\n{history_text}\n{full_prompt}"
        
        response = await router.chat(
            message=full_prompt,
            provider=provider,
            cli_path=cli_path or None
        )
        
        return response
        
    except ImportError:
        logger.error("CLI router not available")
        return "CLI routing is not available. Please use API provider instead."
    except Exception as e:
        logger.error(f"CLI chat error ({provider}): {e}")
        return f"Sorry, I had trouble with the CLI tool. Error: {str(e)[:100]}"


async def _chat_gemini(
    message: str, model: str, api_key: str,
    system_prompt: str, history: list, tools_enabled: bool
) -> str:
    """Chat via Google Gemini."""
    import google.generativeai as genai

    def _sync_call():
        genai.configure(api_key=api_key)

        tools = None
        if tools_enabled:
            try:
                from tools import TOOLS

                function_decls = []
                for t in TOOLS:
                    props = {
                        k: {"type": v["type"], "description": v.get("description", "")}
                        for k, v in t["parameters"].items()
                    }
                    function_decls.append(
                        genai.types.FunctionDeclaration(
                            name=t["name"],
                            description=t["description"],
                            parameters={
                                "type": "object",
                                "properties": props,
                                "required": list(t["parameters"].keys()),
                            },
                        )
                    )
                tools = [genai.types.Tool(function_declarations=function_decls)]
            except Exception as e:
                logger.warning(f"Gemini tools disabled (schema build failed): {e}")
                tools = None

        try:
            gen_model = genai.GenerativeModel(
                model_name=model or "gemini-2.0-flash",
                system_instruction=system_prompt or None,
                tools=tools,
            )
        except TypeError:
            # Older SDKs may not support tools parameter.
            gen_model = genai.GenerativeModel(
                model_name=model or "gemini-2.0-flash",
                system_instruction=system_prompt or None,
            )

        # Build chat history
        chat_history = []
        if history:
            for msg in history[-20:]:  # Last 20 messages (10 turns)
                role = "user" if msg.get("role") == "user" else "model"
                chat_history.append({"role": role, "parts": [msg["content"]]})

        chat_session = gen_model.start_chat(history=chat_history)

        def _extract_text(resp) -> str:
            text = getattr(resp, "text", None)
            if text:
                return text
            try:
                parts = resp.candidates[0].content.parts
                texts = [getattr(p, "text", "") for p in parts if getattr(p, "text", None)]
                return "".join(texts).strip()
            except Exception:
                return ""

        response = chat_session.send_message(message)

        # Tool execution loop (max 3 iterations)
        if tools_enabled and tools is not None:
            try:
                from tools import execute_tool
            except Exception:
                execute_tool = None

            for _ in range(3):
                tool_calls = []
                try:
                    parts = response.candidates[0].content.parts
                    for part in parts:
                        fc = getattr(part, "function_call", None)
                        if fc:
                            tool_calls.append(fc)
                except Exception:
                    tool_calls = []

                if not tool_calls or execute_tool is None:
                    break

                # Execute ALL tool calls and send results together
                response_parts = []
                for fc in tool_calls:
                    try:
                        name = fc.name
                        args = dict(getattr(fc, "args", {}) or {})
                    except Exception:
                        name = getattr(fc, "name", "")
                        args = {}

                    result = execute_tool(name, args)
                    response_parts.append(
                        genai.protos.Part(
                            function_response=genai.protos.FunctionResponse(
                                name=name, response={"result": result}
                            )
                        )
                    )
                response = chat_session.send_message(response_parts)

            final = _extract_text(response)
            return final or "(No response text.)"

        return _extract_text(response) or "(No response text.)"

    return await asyncio.to_thread(_sync_call)


async def _chat_anthropic(
    message: str, model: str, api_key: str,
    system_prompt: str, history: list, tools_enabled: bool
) -> str:
    """Chat via Anthropic Claude."""
    import anthropic

    def _sync_call():
        client = anthropic.Anthropic(api_key=api_key)

        messages = []
        if history:
            for msg in history[-20:]:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg["content"],
                })
        messages.append({"role": "user", "content": message})

        tools = None
        if tools_enabled:
            try:
                from tools import get_anthropic_tools_schema

                tools = get_anthropic_tools_schema()
            except Exception as e:
                logger.warning(f"Anthropic tools disabled (schema build failed): {e}")
                tools = None

        create_kwargs = {
            "model": model or "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "system": system_prompt or "You are a helpful personal assistant.",
            "messages": messages,
        }
        if tools:
            create_kwargs["tools"] = tools

        response = client.messages.create(**create_kwargs)

        if not tools_enabled or tools is None:
            return response.content[0].text

        from tools import execute_tool

        # Tool execution loop (max 3 iterations)
        for _ in range(3):
            if getattr(response, "stop_reason", None) != "tool_use":
                break

            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                break

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for tb in tool_uses:
                result = execute_tool(tb.name, getattr(tb, "input", {}) or {})
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tb.id, "content": result}
                )
            messages.append({"role": "user", "content": tool_results})

            response = client.messages.create(**create_kwargs)

        # Extract final text blocks
        text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        return "".join(text_parts).strip() or response.content[0].text

    return await asyncio.to_thread(_sync_call)


async def _chat_openai(
    message: str, model: str, api_key: str,
    system_prompt: str, history: list, tools_enabled: bool
) -> str:
    """Chat via OpenAI GPT."""
    from openai import OpenAI

    def _sync_call():
        client = OpenAI(api_key=api_key)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            for msg in history[-20:]:
                messages.append({
                    "role": msg.get("role", "user"),
                    "content": msg["content"],
                })
        messages.append({"role": "user", "content": message})

        tools = None
        if tools_enabled:
            try:
                from tools import get_openai_tools_schema

                tools = get_openai_tools_schema()
            except Exception as e:
                logger.warning(f"OpenAI tools disabled (schema build failed): {e}")
                tools = None

        create_kwargs = {
            "model": model or "gpt-4o-mini",
            "messages": messages,
        }
        if tools:
            create_kwargs["tools"] = tools

        response = client.chat.completions.create(**create_kwargs)

        if not tools_enabled or tools is None:
            return response.choices[0].message.content

        from tools import execute_tool

        # Tool execution loop (max 3 iterations)
        for _ in range(3):
            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                break

            if hasattr(msg, "model_dump"):
                messages.append(msg.model_dump())
            else:
                # Fallback: include at least role/content
                messages.append({"role": "assistant", "content": getattr(msg, "content", "")})
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                result = execute_tool(tc.function.name, args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            response = client.chat.completions.create(**create_kwargs)

        return response.choices[0].message.content

    return await asyncio.to_thread(_sync_call)
