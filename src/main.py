# -*- coding: utf-8 -*-
"""
Created on Wed Nov 15 22:14:26 2023

@author: xbox

https://www.youtube.com/watch?v=0TFWtfFY87U
https://www.back4app.com/docs-containers/deployment-process
"""

import uvicorn
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def central_function():
    return{ "eins":"1" }

@app.get("/hello")
def hello_function():
    return{ "hello":"world!" }


if __name__ == "__main__":
    uvicorn.run(app, port=8000, host="0.0.0.0")