"""Multi-round tool execution and streaming policy for the LLM pipeline."""

import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from funcs.animation_pipeline import teach_with_visuals
from funcs.canvas import canvas_update
from funcs.config import config
from funcs.tools import ToolCall, ToolResult

logger = logging.getLogger("llm-pipeline")


class ToolConversationMixin:
    """Provide tool-call execution and response-streaming behavior."""

    @staticmethod
    def _tool_calls_to_assistant_message(
        tool_calls: list[ToolCall], content: str | None = None
    ) -> dict[str, Any]:
        """Build OpenAI-style assistant message containing tool calls."""
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in tool_calls
            ],
        }

    def _is_mutating_tool(self, tool_name: str) -> bool:
        return tool_name in self.MUTATING_TOOL_NAMES

    async def _build_context_and_tools(
        self, user_message: str
    ) -> tuple[list[dict], list[dict] | None]:
        """Assemble conversation context and tool schemas for one turn."""
        context = await self.get_context(user_message)
        context.append({"role": "user", "content": user_message})
        tools_schema = self.get_tools_schema() or None
        return context, tools_schema

    async def _execute_single_tool_call(self, tc: ToolCall):
        """Execute one tool call, including canvas/animation side effects."""
        if tc.name == "canvas_update":
            operations = tc.arguments.get("operations", [])
            result = canvas_update(operations, session_id=self.session_id)

            if self.canvas_callback and result.get("operations"):
                try:
                    await self.canvas_callback(result["operations"])
                except TypeError:
                    self.canvas_callback(result["operations"])

            return ToolResult(
                tool_call_id=tc.id,
                content=result.get("canvas_summary", "Canvas updated"),
                success=True,
            )

        # SDL v2 tool — teach_with_visuals
        if tc.name == "teach_with_visuals":
            result = teach_with_visuals(**tc.arguments, session_id=self.session_id)

            if self.animation_callback and result.get("success"):
                animation_data = {"tool": "teach_with_visuals", "sdl": result["sdl"]}
                try:
                    await self.animation_callback(animation_data)
                except TypeError:
                    self.animation_callback(animation_data)

            return ToolResult(
                tool_call_id=tc.id,
                content=result.get("speech_text", "Visual explanation rendered."),
                success=True,
            )

        return await self.tool_executor.execute_tool_call(tc)

    async def _execute_tool_calls_with_policy(
        self, tool_calls: list[ToolCall]
    ) -> tuple[list[Any], float]:
        """
        Execute tool calls with deterministic policy:
        - mutating tools execute sequentially
        - non-mutating tools may run in parallel
        """
        t_start = time.perf_counter()
        ordered_results: list[Any] = [None] * len(tool_calls)

        if not tool_calls:
            return [], 0.0

        if not config.LLM_PARALLEL_TOOLS or len(tool_calls) == 1:
            for idx, tc in enumerate(tool_calls):
                ordered_results[idx] = await self._execute_single_tool_call(tc)
            return ordered_results, time.perf_counter() - t_start

        pending_indices: list[int] = []
        pending_calls: list[ToolCall] = []

        async def _flush_parallel_batch():
            if not pending_calls:
                return
            batch_results = await self.tool_executor.execute_batch(pending_calls)
            for result_idx, tool_result in enumerate(batch_results):
                ordered_results[pending_indices[result_idx]] = tool_result
            pending_indices.clear()
            pending_calls.clear()

        for idx, tc in enumerate(tool_calls):
            if self._is_mutating_tool(tc.name):
                await _flush_parallel_batch()
                ordered_results[idx] = await self._execute_single_tool_call(tc)
            else:
                pending_indices.append(idx)
                pending_calls.append(tc)

        await _flush_parallel_batch()
        return ordered_results, time.perf_counter() - t_start

    async def chat_with_tools(
        self,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        save_to_memory: bool = True,
        max_tool_rounds: int = 10,
    ) -> str:
        """
        Chat with automatic tool calling.

        Flow:
        1. Send message to LLM with tool schemas
        2. If LLM returns tool_call → fetch tool from DB → execute in sandbox
        3. Return result to LLM
        4. Repeat until LLM returns text response

        Args:
            user_message: User's message
            temperature: Sampling temperature
            max_tokens: Max tokens in response
            save_to_memory: Whether to save to long-term memory
            max_tool_rounds: Max number of tool call rounds (prevents infinite loops)

        Returns:
            Final assistant response after tool execution
        """
        context = await self.get_context(user_message)
        context.append({"role": "user", "content": user_message})

        # Get tool schemas (from DB or in-memory registry)
        tools_schema = self.get_tools_schema() or None

        # Debug: Log tools being sent
        if tools_schema:
            logger.info(
                "Sending %d tools to LLM: %s",
                len(tools_schema),
                [t["function"]["name"] for t in tools_schema],
            )
        else:
            logger.warning("No tools available to send to LLM")

        for round_num in range(max_tool_rounds):
            response = await self.client.complete_with_tools(
                messages=context, tools=tools_schema, temperature=temperature, max_tokens=max_tokens
            )

            # No tool calls - we're done
            if not self.client.has_tool_calls(response):
                result = self.client.get_response_content(response)
                if self.memory:
                    self.memory.process_for_memory(
                        user_message, result, save_semantic=save_to_memory
                    )
                return result

            # LLM wants to call tools
            tool_calls = self.client.parse_tool_calls(response)
            context.append(self.client.get_assistant_message(response))

            logger.info("Round %d: LLM requested %d tool call(s)", round_num + 1, len(tool_calls))

            tool_results, _ = await self._execute_tool_calls_with_policy(tool_calls)
            for tc, tool_result in zip(tool_calls, tool_results, strict=True):
                logger.info("Tool %s result: %s...", tc.name, tool_result.content[:100])

                # Add result to context for LLM
                context.append(self.client.format_tool_result(tool_result))

                # Log for decision memory
                self.log_decision(
                    action=f"tool:{tc.name}", tool=tc.name, success=tool_result.success
                )

        # Exceeded max rounds
        logger.warning("Exceeded max tool rounds (%d)", max_tool_rounds)
        return "I encountered an issue processing your request. Please try again."

    @staticmethod
    def _extract_speech_cues(tool_calls_data: list[dict]) -> str | None:
        """Extract speech text from teach_with_visuals tool call arguments.

        Concatenates 'say' fields into a single TTS-ready string, letting us
        skip the second LLM round entirely.

        Returns None if no speech cues found.
        """
        cues = []
        for tc in tool_calls_data:
            args = tc.get("arguments", {})
            tool_name = tc.get("name", "")
            if tool_name == "teach_with_visuals":
                for step in args.get("steps", []):
                    cue = step.get("say", "")
                    if cue and cue.strip():
                        cues.append(cue.strip())
        if cues:
            return " ".join(cues)
        return None

    async def _chat_with_tools_stream_legacy(
        self,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        save_to_memory: bool = True,
        max_tool_rounds: int = 10,
    ) -> AsyncGenerator[str, None]:
        """
        Streaming chat with tools.

        Optimisation: if tool calls contain speech_cue fields (teaching sequences),
        the spoken explanation is extracted directly and yielded as the TTS text,
        skipping the second LLM inference round entirely. This saves ~3-5s.

        Yields:
            Text chunks of final response
        """
        t_start = time.perf_counter()
        t_llm_total = 0.0
        t_tool_total = 0.0
        all_tool_calls: list[dict] = []
        tokens_in = None
        tokens_out = None
        error_msg = None
        speech_cue_text = None  # extracted from tool calls if available
        t_first_provider_chunk = None
        t_first_text_yield = None
        t_context_start = time.perf_counter()
        t_context_end = None
        t_tools_schema_end = None

        context, tools_schema = await self._build_context_and_tools(user_message)
        t_context_end = time.perf_counter()
        t_tools_schema_end = time.perf_counter()
        tool_names = [t.get("function", {}).get("name") for t in (tools_schema or [])]
        logger.info(
            "chat_with_tools_stream: %d tools passed to LLM: %s", len(tool_names), tool_names
        )

        # Execute tool calls (non-streaming) until we get a text response
        for round_num in range(max_tool_rounds):
            t_llm_start = time.perf_counter()
            response = await self.client.complete_with_tools(
                messages=context, tools=tools_schema, temperature=temperature, max_tokens=max_tokens
            )
            t_llm_total += time.perf_counter() - t_llm_start

            # Extract token usage if available
            if hasattr(response, "usage") and response.usage:
                tokens_in = getattr(response.usage, "prompt_tokens", None)
                tokens_out = getattr(response.usage, "completion_tokens", None)

            if not self.client.has_tool_calls(response):
                logger.info("LLM returned no tool calls — streaming final text response")
                break

            tool_calls = self.client.parse_tool_calls(response)
            logger.info(
                "Round %d: LLM called %d tools: %s",
                round_num + 1,
                len(tool_calls),
                [tc.name for tc in tool_calls],
            )
            context.append(self.client.get_assistant_message(response))

            round_tool_data = []
            tool_results, tool_elapsed = await self._execute_tool_calls_with_policy(tool_calls)
            t_tool_total += tool_elapsed
            for tc, tool_result in zip(tool_calls, tool_results, strict=True):
                tc_data = {"name": tc.name, "arguments": tc.arguments}
                all_tool_calls.append(tc_data)
                round_tool_data.append(tc_data)
                context.append(self.client.format_tool_result(tool_result))
                self.log_decision(
                    action=f"tool:{tc.name}", tool=tc.name, success=tool_result.success
                )

            # Check if we can extract speech cues from this round's tool calls
            extracted = self._extract_speech_cues(round_tool_data)
            if extracted:
                speech_cue_text = extracted

        # ── Yield the response text ──
        t_stream_start = time.perf_counter()
        full_response = ""

        if speech_cue_text:
            # We have speech cues from tool calls — use them directly as TTS text.
            # This skips the entire second LLM inference round.
            logger.info(
                "Using speech_cues as response (%d chars), skipping 2nd LLM round",
                len(speech_cue_text),
            )
            full_response = speech_cue_text
            if t_first_text_yield is None:
                t_first_text_yield = time.perf_counter()
            yield speech_cue_text
        else:
            # No speech cues — fall back to streaming the LLM's text response
            async for chunk in self.client.stream(
                messages=context, temperature=temperature, max_tokens=max_tokens
            ):
                if t_first_provider_chunk is None:
                    t_first_provider_chunk = time.perf_counter()
                full_response += chunk
                if t_first_text_yield is None:
                    t_first_text_yield = time.perf_counter()
                yield chunk

        t_stream_end = time.perf_counter()
        t_end = time.perf_counter()

        if self.memory:
            self.memory.process_for_memory(
                user_message, full_response, save_semantic=save_to_memory
            )

        self._last_call_timing = {
            "latency_total_ms": round((t_end - t_start) * 1000, 2),
            "latency_llm_ms": round(t_llm_total * 1000, 2),
            "latency_tool_ms": round(t_tool_total * 1000, 2),
            "latency_context_ms": round(((t_context_end or t_start) - t_context_start) * 1000, 2),
            "latency_tools_schema_ms": round(
                ((t_tools_schema_end or t_start) - (t_context_end or t_start)) * 1000, 2
            ),
            "latency_first_provider_chunk_ms": (
                round((t_first_provider_chunk - t_start) * 1000, 2)
                if t_first_provider_chunk
                else None
            ),
            "latency_llm_first_token_ms": (
                round((t_first_text_yield - t_start) * 1000, 2) if t_first_text_yield else None
            ),
            "latency_stream_ms": round((t_stream_end - t_stream_start) * 1000, 2),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
        self._last_call_tool_calls = all_tool_calls
        self._last_call_response = full_response
        self._last_call_error = error_msg
        used_cues = "speech_cues" if speech_cue_text else "llm_stream"
        logger.info(
            "Call metrics (%s): total=%sms, llm=%sms, tools=%sms, stream=%sms",
            used_cues,
            self._last_call_timing["latency_total_ms"],
            self._last_call_timing["latency_llm_ms"],
            self._last_call_timing["latency_tool_ms"],
            self._last_call_timing["latency_stream_ms"],
        )

    async def chat_with_tools_stream(
        self,
        user_message: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        save_to_memory: bool = True,
        max_tool_rounds: int = 10,
    ) -> AsyncGenerator[str, None]:
        """
        Streaming chat with tool support.

        - Legacy path: non-streaming planner rounds + final stream.
        - New path (flagged): stream_with_tools orchestration so text can be emitted earlier.
        """
        if not config.LLM_STREAM_TOOL_ORCHESTRATION:
            async for chunk in self._chat_with_tools_stream_legacy(
                user_message=user_message,
                temperature=temperature,
                max_tokens=max_tokens,
                save_to_memory=save_to_memory,
                max_tool_rounds=max_tool_rounds,
            ):
                yield chunk
            return

        t_start = time.perf_counter()
        t_context_start = t_start
        t_context_end = None
        t_tools_schema_end = None
        t_llm_total = 0.0
        t_tool_total = 0.0
        t_stream_start = None
        t_first_provider_chunk = None
        t_first_text_yield = None
        tokens_in = None
        tokens_out = None
        error_msg = None
        speech_cue_text = None
        all_tool_calls: list[dict] = []
        full_response = ""

        try:
            context, tools_schema = await self._build_context_and_tools(user_message)
            t_context_end = time.perf_counter()
            t_tools_schema_end = time.perf_counter()
            tool_names = [t.get("function", {}).get("name") for t in (tools_schema or [])]
            logger.info(
                "chat_with_tools_stream(orchestrated): %d tools passed to LLM: %s",
                len(tool_names),
                tool_names,
            )

            exhausted_rounds = True
            for round_num in range(max_tool_rounds):
                round_tool_calls: list[ToolCall] = []

                t_llm_start = time.perf_counter()
                provider_stream = self.client.stream_with_tools(
                    messages=context,
                    tools=tools_schema,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                async for event in self.client.iter_stream_tool_events(provider_stream):
                    if t_first_provider_chunk is None:
                        t_first_provider_chunk = time.perf_counter()

                    event_type = event.get("type")
                    if event_type == "text_delta":
                        text = event.get("text") or ""
                        if not text:
                            continue
                        full_response += text
                        if t_first_text_yield is None:
                            t_first_text_yield = time.perf_counter()
                            t_stream_start = t_first_text_yield
                        yield text
                    elif event_type == "tool_call_done":
                        round_tool_calls = event.get("tool_calls") or []
                    elif event_type == "usage":
                        tokens_in = event.get("tokens_in", tokens_in)
                        tokens_out = event.get("tokens_out", tokens_out)

                t_llm_total += time.perf_counter() - t_llm_start

                if not round_tool_calls:
                    exhausted_rounds = False
                    logger.info(
                        "Round %d produced no tool calls — response streaming complete",
                        round_num + 1,
                    )
                    break

                logger.info(
                    "Round %d (streamed) called %d tools: %s",
                    round_num + 1,
                    len(round_tool_calls),
                    [tc.name for tc in round_tool_calls],
                )
                context.append(
                    self._tool_calls_to_assistant_message(
                        round_tool_calls,
                        content=None,
                    )
                )

                round_tool_data = []
                for tc in round_tool_calls:
                    tc_data = {"name": tc.name, "arguments": tc.arguments}
                    round_tool_data.append(tc_data)
                    all_tool_calls.append(tc_data)

                tool_results, tool_elapsed = await self._execute_tool_calls_with_policy(
                    round_tool_calls
                )
                t_tool_total += tool_elapsed
                for tc, tool_result in zip(round_tool_calls, tool_results, strict=True):
                    context.append(self.client.format_tool_result(tool_result))
                    self.log_decision(
                        action=f"tool:{tc.name}", tool=tc.name, success=tool_result.success
                    )

                extracted = self._extract_speech_cues(round_tool_data)
                if extracted:
                    exhausted_rounds = False
                    speech_cue_text = extracted
                    break

            if exhausted_rounds and not speech_cue_text:
                logger.warning(
                    "Exceeded max tool rounds (%d) in orchestrated path; falling back to final text stream",
                    max_tool_rounds,
                )
                async for chunk in self.client.stream(
                    messages=context,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    if t_first_provider_chunk is None:
                        t_first_provider_chunk = time.perf_counter()
                    full_response += chunk
                    if t_first_text_yield is None:
                        t_first_text_yield = time.perf_counter()
                        t_stream_start = t_first_text_yield
                    yield chunk

            if speech_cue_text:
                if full_response and not full_response.endswith(" "):
                    full_response += " "
                full_response += speech_cue_text
                if t_first_text_yield is None:
                    t_first_text_yield = time.perf_counter()
                    t_stream_start = t_first_text_yield
                yield speech_cue_text

            if self.memory:
                self.memory.process_for_memory(
                    user_message, full_response, save_semantic=save_to_memory
                )

        except Exception as e:
            error_msg = str(e)
            logger.exception("chat_with_tools_stream(orchestrated) error: %s", e)
            raise
        finally:
            t_end = time.perf_counter()
            self._last_call_timing = {
                "latency_total_ms": round((t_end - t_start) * 1000, 2),
                "latency_llm_ms": round(t_llm_total * 1000, 2),
                "latency_tool_ms": round(t_tool_total * 1000, 2),
                "latency_context_ms": round(
                    ((t_context_end or t_start) - t_context_start) * 1000, 2
                ),
                "latency_tools_schema_ms": round(
                    ((t_tools_schema_end or t_start) - (t_context_end or t_start)) * 1000, 2
                ),
                "latency_first_provider_chunk_ms": (
                    round((t_first_provider_chunk - t_start) * 1000, 2)
                    if t_first_provider_chunk
                    else None
                ),
                "latency_llm_first_token_ms": (
                    round((t_first_text_yield - t_start) * 1000, 2) if t_first_text_yield else None
                ),
                "latency_stream_ms": (
                    round((t_end - t_stream_start) * 1000, 2) if t_stream_start else 0.0
                ),
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
            }
            self._last_call_tool_calls = all_tool_calls
            self._last_call_response = full_response
            self._last_call_error = error_msg
            logger.info(
                "Call metrics (orchestrated): total=%sms, llm=%sms, tools=%sms, ttft=%sms",
                self._last_call_timing["latency_total_ms"],
                self._last_call_timing["latency_llm_ms"],
                self._last_call_timing["latency_tool_ms"],
                self._last_call_timing.get("latency_llm_first_token_ms"),
            )
