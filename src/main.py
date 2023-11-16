# -*- coding: utf-8 -*-
"""
Created on Wed Nov 15 22:14:26 2023

@author: xbox

https://www.youtube.com/watch?v=0TFWtfFY87U
https://www.back4app.com/docs-containers/deployment-process
"""

import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from typing import Optional
import json

app = FastAPI()

class Deck(BaseModel):
    id: int
    creator: str
    owner: Optional[int] = ''
    dealtOut: bool = False

@app.get( '/find', status_code=200 )    
def find_deck(  d_id: Optional[int] = Query( None, title='DID', description='The Deckid from QR-Code' ),
                creator: Optional[str] = Query( None, title='DCN', description='The name of the creator of the submitted deck' ),
                owner: Optional[str] = Query( None, title='DON', description='The name of the new owner of the submitted deck' ),
                dealtOut: Optional[bool] = Query( None, title='OUT', description='Status, if the deck is assigned a new owner' ),
                ):
    tmp_decks = decks
    if d_id:
        tmp_decks = [ d for d in tmp_decks if d['id'] == d_id ]
    if creator:
        tmp_decks = [ d for d in tmp_decks if d['creator'] == creator ]
    if owner:
        tmp_decks = [ d for d in tmp_decks if d['owner'] == owner ]
    if dealtOut:
        tmp_decks = [ d for d in tmp_decks if d['id'] == dealtOut ]
    
    return tmp_decks

@app.get( '/addDeck', status_code=201 )
def add_deck( d_id: int = Query( None, title='DID', description='The Deckid from QR-Code' ),
              creator: str = Query( None, title='DCN', description='The name of the creator of the submitted deck' ) ):
    # add a deck to the database via link in QR Code
    new_deck = {
            "id": d_id,
            "creator": creator,
            "owner": "",
            "dealtOut": False
        }
    decks.append( new_deck )
    
    with open( 'raffle.json', 'w' ) as f:
        json.dump( decks, f )
        
    return new_deck

def start_raffle():
    # manually starts the raffle, this stops registration access and shuffles
    pass

def dealout_deck():
    pass

try:
    with open( 'raffle.json', 'r' ) as f:
        decks = json.load(f)['decks']
except:
    decks = json.loads('[]')

if __name__ == "__main__":
    uvicorn.run(app, port=8000, host="0.0.0.0")