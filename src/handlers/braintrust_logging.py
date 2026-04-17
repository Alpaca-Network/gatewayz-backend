"""Braintrust span logging for chat completions."""

import logging

logger = logging.getLogger(__name__)


def _sanitize_content(content):
    """Sanitize content to avoid NoneType errors in Braintrust SDK."""
    if content is None:
        return ""
    elif isinstance(content, str):
        return content
    elif isinstance(content, list):
        texts = []
        for item in content:
            if item is None:
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if text is not None:
                    texts.append(str(text))
            else:
                texts.append(str(item))
        return " ".join(t for t in texts if t)
    else:
        return str(content)


def _prepare_chat_messages(messages):
    """Convert chat completion messages to loggable format, safely handling None values."""
    messages_for_log = []
    for m in messages:
        if m is None:
            continue
        msg_dict = m.model_dump() if hasattr(m, "model_dump") else m
        if msg_dict is None:
            continue
        # Sanitize content to avoid NoneType subscript errors in Braintrust SDK
        if isinstance(msg_dict, dict) and "content" in msg_dict:
            content = msg_dict.get("content")
            if content is None:
                msg_dict["content"] = ""
            elif isinstance(content, list):
                # Filter out None items and sanitize nested dicts in content list
                sanitized_content = []
                for item in content:
                    if item is None:
                        continue
                    if isinstance(item, dict):
                        # Deep sanitize dict items (e.g., {"type": "text", "text": None})
                        sanitized_item = {}
                        for k, v in item.items():
                            if v is None:
                                sanitized_item[k] = "" if k in ("text", "content") else v
                            else:
                                sanitized_item[k] = v
                        sanitized_content.append(sanitized_item)
                    else:
                        sanitized_content.append(item)
                msg_dict["content"] = sanitized_content
        messages_for_log.append(msg_dict)
    return messages_for_log


def _prepare_response_messages(input_messages):
    """Convert unified response input messages to loggable format, safely handling None values."""
    result = []
    for inp_msg in input_messages:
        if inp_msg is None:
            continue
        content = inp_msg.content
        if content is None:
            content = ""
        elif isinstance(content, list):
            # Safely extract text from multimodal content, filtering None items
            text_parts = []
            for item in content:
                if item is None:
                    continue
                if isinstance(item, dict):
                    # Extract text field if present
                    text = item.get("text")
                    if text is not None:
                        text_parts.append(str(text))
                elif isinstance(item, str):
                    text_parts.append(item)
            content = " ".join(text_parts) if text_parts else ""
        elif not isinstance(content, str):
            content = str(content)
        result.append({"role": inp_msg.role, "content": content})
    return result


def _extract_chat_output(processed):
    """Safely extract output content from chat completions response."""
    bt_choices = processed.get("choices") or []
    bt_first_choice = bt_choices[0] if bt_choices else None
    bt_message = (
        bt_first_choice.get("message") if isinstance(bt_first_choice, dict) else None
    )
    bt_content = bt_message.get("content") if isinstance(bt_message, dict) else None
    return _sanitize_content(bt_content)


def _extract_response_output(response):
    """Safely extract output content from unified response."""
    bt_output = ""
    output_list = response.get("output")
    if isinstance(output_list, list) and len(output_list) > 0:
        first_output = output_list[0]
        if isinstance(first_output, dict):
            bt_content = first_output.get("content")
            bt_output = _sanitize_content(bt_content)
    return bt_output


async def log_to_braintrust(
    span,
    messages,
    processed_response,
    model,
    provider,
    user,
    trial,
    session_id,
    prompt_tokens,
    completion_tokens,
    total_tokens,
    elapsed,
    cost,
    request_id,
    endpoint,
    check_braintrust_available,
    braintrust_flush,
    **extra_metadata
) -> None:
    """Log a chat completion span to Braintrust."""
    try:
        log_parts = [
            f"[Braintrust] Starting log for request_id={request_id}, model={model}",
        ]
        if endpoint:
            log_parts.append(f"endpoint={endpoint}")
        log_parts.append(f"available={check_braintrust_available()}")
        log_parts.append(f"span_type={type(span).__name__}")
        logger.info(", ".join(log_parts))

        # Prepare input messages based on endpoint type
        if endpoint:
            # unified_responses: messages are ResponseMessage objects with .content/.role
            input_for_log = _prepare_response_messages(messages)
            bt_output = _extract_response_output(processed_response)
        else:
            # chat_completions: messages are ProxyRequest message objects
            input_for_log = _prepare_chat_messages(messages)
            bt_output = _extract_chat_output(processed_response)

        # Safely get user_id and environment for anonymous users (user=None)
        bt_user_id = user["id"] if user else "anonymous"
        bt_environment = user.get("environment_tag", "live") if user else "live"
        bt_is_trial = trial.get("is_trial", False) if trial else False
        logger.info(
            f"[Braintrust] Logging span: user_id={bt_user_id}, model={model}, tokens={total_tokens}"
        )

        metadata = {
            "model": model,
            "provider": provider,
            "user_id": bt_user_id,
            "session_id": session_id,
            "is_trial": bt_is_trial,
            "environment": bt_environment,
        }
        if endpoint:
            metadata["endpoint"] = endpoint

        span.log(
            input=input_for_log,
            output=bt_output,
            metrics={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "latency_ms": int(elapsed * 1000),
                "cost_usd": cost if not bt_is_trial else 0.0,
            },
            metadata=metadata,
        )
        span.end()
        # Flush to ensure data is sent to Braintrust
        braintrust_flush()
        logger.info(
            f"[Braintrust] Successfully logged and flushed span for request_id={request_id}"
        )
    except Exception as e:
        logger.warning(f"[Braintrust] Failed to log to Braintrust: {e}", exc_info=True)
