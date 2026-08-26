from ollama import Client, ResponseError
import time as t
text = input("prompt: ")
import subprocess


start_time = t.time()

client = Client(host='http://localhost:11434')

response = client.chat(
    model='zars-local',
    options={"temperature": 5, "seed": 42, "top_k": 1},
    messages=[
        {
        'role': 'system',
        'content': """You are talking direclty to teh powershell, teh user will give an instruction, simply return with powershell command to do what teh user asks, if u cant simply return with error (then an apology+)"""
    },

        {
        'role': 'user',
        'content': text
    }]
)
command = response['message']['content']
if command.startswith("`"):
    command = command.strip("`").replace("powershell", "", 1).strip()
    
print(f"Running: {command}")

result = subprocess.run(
    ["powershell", "-NoProfile", "-Command", command],
    capture_output=True,
    text=True,
    shell=True
)

print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
print("Exit code:", result.returncode)