from g4f.client import Client
import sys
text = sys.argv[1]
client = Client()
response = client.chat.completions.create(
    model="gpt-4", 
    messages=[{"role": "user", "content": f"Search the web and summarize to less than 100 words: {text}"}]
)
print(response.choices[0].message.content)