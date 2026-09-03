from g4f.client import Client as G4fClient
from ollama import Client as OllamaClient
import requests
import time as t

test_times = ["one", "two", "three"]
local_times = []
g4f_times = []
query = "Latest in TEch todAY"
for item in test_times:

    print("Test ", item)
    starttime_g4f = t.time()
    g4f_client = G4fClient()
    response = g4f_client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": f"Search the web and summarize returning only with page content: {query}"}]
    )
    time_g4f = t.time() - starttime_g4f
    print("g4f:", response.choices[0].message.content)
    print(f"g4f time: {time_g4f:.2f}s")
    g4f_times.append(f"g4f time: {time_g4f:.2f}s")

    resp = requests.post(
        "https://lite.duckduckgo.com/lite/",
        data={"q": query},
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=20,
    )

    starttime_local = t.time()
    ollama_client = OllamaClient(host='http://localhost:11434')
    response_llm = ollama_client.chat(
        model='zars-local',
        options={"temperature": 0, "seed": 42, "top_k": 1},
        messages=[
            {'role': 'system', 'content': "summarize returning only with page content"},
            {'role': 'user', 'content': resp.text}
        ]
    )
    time_local = t.time() - starttime_local
    print("Local:", response_llm["message"]["content"])
    print(f"Local time: {time_local:.2f}s")
    local_times.append(f"Local time: {time_local:.2f}s")

print(f"g4f times:{g4f_times}")
print(f"Local times: {local_times}")
