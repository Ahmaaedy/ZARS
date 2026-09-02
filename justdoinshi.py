from openrouter import OpenRouter
import os
import time as t
start_time = t.time()
with OpenRouter(
    api_key="REMOVED_OPENROUTER_KEY"
) as open_router:
    res = open_router.chat.send(
        model="cohere/north-mini-code:free",
        messages=[
            {"content": "Hello", "role": "user"},
        ],
        stream=False,
    )
    end_time = t.time()
    print(end_time - start_time)
    print(res)