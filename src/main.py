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

global raffleRdy
raffleRdy = False

@app.get( '/restart', status_code=200 )
def clear_json():
    decks = read_json( 'raffle.json' )
    decks.at[0,'dealtOut']
    decks = DataFrame()
    decks.to_json( 'raffle.json' )
    return decks
    
@app.get( '/status', status_code=200 )
def get_status():
    decks = read_json( 'raffle.json' )
    if decks.at[0,'dealtOut']:
        return "Raffle is rdy to start!\n {} have been handed out to ther new onwer.".format( decks.loc[:,'dealtOut'].sum() )
    else:
        return f"Registration is still ongoing. {len(decks)} have been registered yet."
    # registered deck count anzeigen!!!!

# class Deck(BaseModel):
#     id: int
#     creator: str
#     owner: Optional[int] = ''
#     dealtOut: bool = False

@app.get( '/find', status_code=200 )    
def find_deck(  d_id: Optional[int] = Query( None, title='DID', description='The Deckid from QR-Code' ),
                creator: Optional[str] = Query( None, title='DCN', description='The name of the creator of the submitted deck' ),
                owner: Optional[str] = Query( None, title='DON', description='The name of the new owner of the submitted deck' ),
                dealtOut: Optional[bool] = Query( None, title='OUT', description='Status, if the deck is assigned a new owner' ),
                ):
    tmp_decks = decks
    # if d_id:
    #     tmp_decks = [ d for d in tmp_decks if d['id'] == d_id ]
    # if creator:
    #     tmp_decks = [ d for d in tmp_decks if d['creator'] == creator ]
    # if owner:
    #     tmp_decks = [ d for d in tmp_decks if d['owner'] == owner ]
    # if dealtOut:
    #     tmp_decks = [ d for d in tmp_decks if d['id'] == dealtOut ]
    
    return tmp_decks

@app.get( '/addDeck', status_code=201 )
def add_deck( d_id: int = Query( None, title='DID', description='The Deckid from QR-Code' ),
              creator: str = Query( None, title='DCN', description='The name of the creator of the submitted deck' ) ):
    # add a deck to the database via link in QR Code
    decks = read_json( 'raffle.json' )
    if raffleRdy:
        return "Registration closed. Decks get dealtout now!"
    if d_id in decks.index.values:
        return "This deck is already registred!"
    else:
        new_deck = DataFrame( [ { "id": d_id,
                                   "creator": creator,
                                   "owner": "",
                                   "dealtOut": False
                                   } ] )
        new_deck = new_deck.set_index( 'id' )
        decks = concat( [decks, new_deck] )
        decks.to_json( 'raffle.json' )
        return new_deck

def shuffle_decks(decks):
    creatorOrder = decks.index.values.tolist()
    giftOrder = decks.index.values.tolist()
    shuffle( creatorOrder )
    shuffle( giftOrder )
    if sum( [ 0 if (i-j) else 1 for i,j in zip(giftOrder, creatorOrder)  ] ):
        giftOrder, creatorOrder = shuffle_decks()
    else:
        return giftOrder, creatorOrder
    
@app.get( '/start', status_code=200 )
def start_raffle():
    # manually starts the raffle, this stops registration access and shuffles
    decks = read_json( 'raffle.json' )
    decks.at[0,'dealtOut'] = True #newRaffleRdy Bool
    gOrder, cOrder = shuffle_decks( len(decks) )
    
    for gifted, gifter in zip( gOrder, cOrder ):
        decks.at[ gifter, 'owner' ] = gifted
    decks.to_json( 'raffle.json' )
    return get_status() 
                
@app.get( '/deal', status_code=201 )
def dealout_deck( d_id: int = Query( None, title='DID', description='The Deckid from QR-Code' ) ):
    decks = read_json( 'raffle.json' )
    if not raffleRdy:
        return "Not all Decks yet Registred! Please wait until the Raffle starts."
    else:
        decks.at[d_id,'dealtOut'] = True
        decks.to_json( 'raffle.json' )
        return f"Please hand this deck over to {decks.at[d_id,'owner']}!"


if __name__ == "__main__":
    uvicorn.run(app, port=8000, host="0.0.0.0")