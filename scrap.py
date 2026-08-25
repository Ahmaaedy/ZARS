from ollama import Client, ResponseError
import time as t
text = input("prompt: ")
import subprocess


start_time = t.time()

# Initialize the client pointing to your local server
client = Client(host='http://localhost:11434')

response = client.chat(
    model='zars-local',
    messages=[
        {
        'role': 'system',
        'content': 'Refrain from using '' to close commands'
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