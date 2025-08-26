import gradio as gr

def greet(name):
    return "This is test.py    " + name + "!!!!!!!!!!!"

demo = gr.Interface(fn=greet, inputs="text", outputs="text")
demo.launch() 