import os
from dotenv import load_dotenv
import openai

load_dotenv()
api_key = os.getenv("OPENROUTER_API_KEY")
if not api_key:
    raise SystemExit("OPENROUTER_API_KEY is missing. Add it to .env and try again.")

client = openai.OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
)

def ask(prompt : str) -> str:
    response = client.chat.completions.create(
    model="qwen/qwen3.5-flash-02-23",
    messages=[
        {"role": "user", "content": prompt},
        {"role": "system", "content": "你是一个猫娘,请用猫娘的口吻回答问题,并且在回答中加入猫娘的拟声词"},
    ],
    max_tokens=3000,
)
    return response.choices[0].message.content

prompt_a = "解释一下python的list"
prompt_b = "你是一个猫娘教师,解释一下python的list,在150词以内"
prompt_c = "你是一个猫娘教师,解释一下python的list,在150词以内. 内容包含定义和易错点"

if __name__ == "__main__":
    prompts = {
        "Level A (Vague)": prompt_a,
        "Level B (Structured)": prompt_b,
        "Level C (Precise)": prompt_c,
    }

    for level, prompt in prompts.items():
        if not prompt:
            print(f"{level}: Empty -> Fill in the TODO !")
            continue
        print(f"Prompt: {prompt[:80]}{'...' if len(prompt) > 80 else ''}")
        answer = ask(prompt)
        print(f"Anser: {answer}")

