# -*- coding: utf-8 -*-
"""
Created on Wed Nov 15 22:14:26 2023

@author: xbox

https://www.youtube.com/watch?v=0TFWtfFY87U
https://www.back4app.com/docs-containers/deployment-process
"""

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import json

app = FastAPI()

class Deck(BaseModel):
    id: int
    creator: str
    owner: Optional[int] = ''
    dealtOut: bool = False

@app.post( '/addDeck', status_code=201 )
def add_deck(deck:Deck):
    # add a deck to the database via link in QR Code
    new_deck = {
            "id": deck.creator,
            "creator": deck.creator,
            "owner": "",
            "dealtOut": False
        }
    decks.append( new_deck )
    
    with open( 'raffle.json', 'w' ) as f:
        json.dump( decks, f )
        
    print( new_deck )

def start_raffle():
    # manually starts the raffle, this stops registration access and shuffles
    pass

def dealout_deck():
    pass

try:
    with open( 'raffle.json', 'r' ) as f:
        decks = json.load(f)
except:
    decks = json.loads('{}')

print(decks)

if __name__ == "__main__":
    uvicorn.run(app, port=8080, host="0.0.0.0")