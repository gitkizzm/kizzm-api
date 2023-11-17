# -*- coding: utf-8 -*-
"""
Created on Wed Nov 15 22:14:26 2023

@author: xbox

https://www.youtube.com/watch?v=0TFWtfFY87U
https://www.back4app.com/docs-containers/deployment-process
"""

import uvicorn
from fastapi import FastAPI, Query
# from pydantic import BaseModel
from typing import Optional
from random import shuffle
from pandas import DataFrame, read_json, concat
# import json

app = FastAPI()

@app.get( '/restart', status_code=200 )
def clear_json():
    new_deck = DataFrame( [ { "id": 0,
                               "creator": "",
                               "owner": "",
                                "dealtOut": False
                               } ] )
    new_deck = new_deck.set_index( 'id' )
    new_deck.to_json( 'raffle.json' )
    return "Raffle restarted. All Data cleared!"
    
@app.get( '/status', status_code=200 )
def get_status():
    decks = read_json( 'raffle.json' )
    if decks.at[0,'dealtOut']:
        return "Raffle is rdy to start!"
    else:
        return f"Registration is still ongoing. {len(decks)-1} have been registered yet."
    # registered deck count anzeigen!!!!

# class Deck(BaseModel):
#     id: int
#     creator: str
#     owner: Optional[int] = ''
#     dealtOut: bool = False

@app.get( '/find', status_code=200 )    
def find_deck(  d_id: Optional[int] = Query( None, title='DID', description='The Deckid from QR-Code' ),
                creator: Optional[str] = Query( None, title='DCN', description='The name of the creator of the submitted deck' ),
                owner: Optional[str] = Query( None, title='DON', description='The name of the new owner of the submitted deck' )
                ):
    tmp_decks = read_json( 'raffle.json' )
    if d_id:
        tmp_decks = tmp_decks[ tmp_decks.index.isin( [ d_id ] ) ]
    if creator:
        tmp_decks = tmp_decks[ tmp_decks['creator'].isin( [ creator ] ) ]
    if owner:
        tmp_decks = tmp_decks[ tmp_decks['owner'].isin( [ owner ] ) ]
    
    return tmp_decks.to_string()

@app.get( '/addAll', status_code=201 )
def add_deck1():
    add_deck( 1, 'Julian Dürr' )
    add_deck( 2, 'Steven' )
    add_deck( 3, 'Sidney' )
    add_deck( 4, 'Julien' )
    add_deck( 5, 'Daniel' )
    add_deck( 6, 'Basti' )
    return "6 Decks Added!"
    

@app.get( '/addDeck', status_code=201 )
def add_deck( d_id: int = Query( None, title='DID', description='The Deckid from QR-Code' ),
              creator: str = Query( None, title='DCN', description='The name of the creator of the submitted deck' ) ):
    # add a deck to the database via link in QR Code
    decks = read_json( 'raffle.json' )
    if decks.at[0,'dealtOut']:
        return "Registration closed. Decks get dealtout now!"
    if d_id in decks.index.values:
        return "This deck is already registred!"
    else:
        decks.at[ d_id, "creator" ] = creator
        decks.at[ d_id, "owner" ] = ""
        decks.at[ d_id, "dealtOut" ] = False
        decks.to_json( 'raffle.json' )
        return "hat geklappt"

def shuffle_decks(decks):
    creatorOrder = decks.index.values.tolist()
    giftOrder = decks.index.values.tolist()
    creatorOrder.remove(0)
    giftOrder.remove(0)
    shuffleCount = 0
    while sum( [ 0 if (i-j) else 1 for i,j in zip(giftOrder, creatorOrder)  ] ):
        shuffle( creatorOrder )
        shuffle( giftOrder )
        shuffleCount+=1
        print('Shuffle Count is {}'.format( shuffleCount ))
    else:
        return giftOrder, creatorOrder
    
@app.get( '/start', status_code=200 )
def start_raffle():
    # manually starts the raffle, this stops registration access and shuffles
    decks = read_json( 'raffle.json' )
    decks.at[0,'dealtOut'] = True #newRaffleRdy Bool
    gOrder, cOrder = shuffle_decks( decks )
    
    for gifted, gifter in zip( gOrder, cOrder ):
        decks.at[ gifter, 'owner' ] = decks.at[ gifted, 'creator' ]
    decks.to_json( 'raffle.json' )
    return get_status() 
                
@app.get( '/deal', status_code=201 )
def dealout_deck( d_id: int = Query( None, title='DID', description='The Deckid from QR-Code' ) ):
    decks = read_json( 'raffle.json' )
    if not decks.at[0,'dealtOut']:
        return "Not all Decks yet Registred! Please wait until the Raffle starts."
    else:
        decks.at[d_id,'dealtOut'] = True
        decks.to_json( 'raffle.json' )
        name = decks.at[d_id,'owner']
        return f"Please hand this deck over to {name}!"


if __name__ == "__main__":
    new_deck = DataFrame( [ { "id": 0,
                               "creator": "",
                               "owner": "",
                               "dealtOut": False
                               } ] )
    new_deck = new_deck.set_index( 'id' )
    new_deck.to_json( 'raffle.json' )
    del new_deck
    uvicorn.run(app, port=8080, host="0.0.0.0")