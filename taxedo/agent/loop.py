import json


async def run_agent(create_message, request, tools, execute, *, max_steps=5):
    request = dict(
        request,
        messages=list(request["messages"]),
        tools=tools,
        tool_choice={"type": "auto", "disable_parallel_tool_use": True},
    )
    seen = set()
    for step in range(max_steps):
        if step == max_steps - 1:
            # Last step: require an answer so no tool result is wasted.
            request["tool_choice"] = {"type": "none"}
        response = await create_message(request)
        calls = [
            block
            for block in response.content
            if getattr(block, "type", None) == "tool_use"
        ]
        if not calls:
            if getattr(response, "stop_reason", "end_turn") not in (None, "end_turn"):
                raise ValueError("Agent response was incomplete")
            return response
        if len(calls) != 1 or response.stop_reason != "tool_use":
            raise ValueError("Expected one agent tool call")
        call = calls[0]
        if call.name not in {tool["name"] for tool in tools} or not isinstance(
            call.input, dict
        ):
            raise ValueError("Unknown agent tool or invalid arguments")
        signature = json.dumps([call.name, call.input], sort_keys=True)
        failed = False
        if signature in seen:
            data = {
                "error": (
                    "This call was already made. Use the previous result or "
                    "try a different query."
                )
            }
        else:
            seen.add(signature)
            try:
                data = await execute(call.name, call.input)
            except ValueError as error:
                # Rejected arguments are the model's to correct, not fatal.
                data = {"error": str(error)}
                failed = True
        request["messages"] += [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.input,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(data, ensure_ascii=False),
                        "is_error": failed,
                    }
                ],
            },
        ]
    raise ValueError("Agent reached its tool-step limit; no result was saved")


SEARCH_TOOL = {
    "name": "search_tax_references",
    "description": (
        "Search local tax evidence. Refine the query when initial evidence "
        "is incomplete or conflicting."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 3, "maxLength": 300}},
        "required": ["query"],
        "additionalProperties": False,
    },
}


def reference_query(arguments):
    query = arguments.get("query")
    if (
        set(arguments) != {"query"}
        or not isinstance(query, str)
        or not 3 <= len(query.strip()) <= 300
    ):
        raise ValueError("Invalid reference query")
    return query.strip()
