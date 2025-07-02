from aworld.prompt import Prompt


def main():
    # Read the general.txt file in the current directory
    template = ""
    with open("general.txt", "r") as f:
        template = f.read()

    print(template)
    print("====================")
    prompt = Prompt().get_prompt()
    print(prompt)

if __name__ == "__main__":
    main()