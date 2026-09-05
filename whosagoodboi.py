from ollama import Client, web_search, web_fetch
import time as t
client = Client(host="http://127.0.0.1:11434")  # address baked in here
tools = {"web_search": web_search, "web_fetch": web_fetch}
messages = [{"role": "user", "content": "how do i fry akara"}]

while True:
    start_time = t.time() 
    response = client.chat(model="gemma4:12b", messages=messages, tools=[web_search, web_fetch])
    messages.append(response.message)

    if not response.message.tool_calls:
        print(response.message.content)
        print("Time: ", t.time() - start_time)
        break

    for call in response.message.tool_calls:
        result = tools[call.function.name](**call.function.arguments)
        messages.append({"role": "tool", "content": str(result), "tool_name": call.function.name})