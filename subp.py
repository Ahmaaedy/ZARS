import subprocess
res = subprocess.run(["python", "--version"], capture_output=True, text=True)
print(res.stdout)