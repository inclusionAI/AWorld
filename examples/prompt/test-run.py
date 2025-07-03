from aworld.prompt import Prompt
from examples.prompt.general_prompt import sef_prompt


def main():
    # Read the general.txt file in the current directory
    template = sef_prompt


    print(template)
    print("====================")
    prompt = Prompt(template).get_prompt()
    print(prompt)

if __name__ == "__main__":
    main()