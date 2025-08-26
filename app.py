import gradio as gr

def greet(name):
    return "KKK " + name + "!!"

demo = gr.Interface(fn=greet, inputs="text", outputs="text")
demo.launch() 