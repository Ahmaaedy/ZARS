import subprocess

ps_command = "echo hello_world"

# Open the process and stream directly to Python's console
process = subprocess.Popen(
    ["powershell", "-Command", ps_command],
    stdout=None,  # Sends output directly to your current terminal
    stderr=None
)

# Wait for the command to finish
process.wait()
