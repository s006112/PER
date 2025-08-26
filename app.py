import gradio as gr

def greet(name):
    return "Hello KASDASDASDN " + name + "!!"

demo = gr.Interface(fn=greet, inputs="text", outputs="text")
demo.launch() 