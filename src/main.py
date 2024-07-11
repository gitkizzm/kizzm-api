# -*- coding: utf-8 -*-
"""
Created on Wed Nov 15 22:14:26 2023

@author: xbox

https://www.youtube.com/watch?v=0TFWtfFY87U
https://www.back4app.com/docs-containers/deployment-process

form tinkering
    https://github.com/itsthatianguy/youtube/blob/main/fastapi-forms-file-upload/app.py
    https://www.youtube.com/watch?v=L4WBFRQB7Lk
"""

import uvicorn
from fastapi import FastAPI, Request, Form, Depends, UploadFile, File, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from schemas import AwesomeForm
from typing import Optional
from random import shuffle
from pandas import DataFrame, read_json
import os


app = FastAPI()



script_dir = os.path.dirname(__file__)
st_abs_file_path = os.path.join(script_dir, "static/")
tmplt_abs_file_path = os.path.join(script_dir, "templates")
templates = Jinja2Templates(directory=tmplt_abs_file_path)
app.mount("/static", StaticFiles(directory=st_abs_file_path), name="static")

@app.get('/basic', response_class=HTMLResponse)
def get_basic_form(request: Request):
    return templates.TemplateResponse("basic-form.html", {"request": request})

@app.post('/basic', response_class=HTMLResponse)
async def post_basic_form(request: Request, username: str = Form(...), password: str = Form(...), file: UploadFile = File(...)):
    print(f'username: {username}')
    print(f'password: {password}')
    content = await file.read()
    print(content)
    return templates.TemplateResponse("basic-form.html", {"request": request})

@app.get('/awesome', response_class=HTMLResponse)
def get_form(request: Request):
    return templates.TemplateResponse("awesome-form.html", {"request": request})

@app.post('/awesome', response_class=HTMLResponse)
def post_form(request: Request, form_data: AwesomeForm = Depends(AwesomeForm.as_form)):
    print(form_data)
    return templates.TemplateResponse("awesome-form.html", {"request": request})

@app.get( '/restart', status_code=200 )
def clear_json():
    new_deck = DataFrame( [ { "id": 0,
                               "creator": "",
                               "owner": "",
                                "dealtOut": False
                               } ] )
    new_deck = new_deck.set_index( 'id' )
    new_deck.to_json( 'raffle.json' )
    return "Commander Secret Santa restarted. All Data cleared!"
    
@app.get( '/status', status_code=200 )
def get_status():
    decks = read_json( 'raffle.json' )
    if decks.at[0,'dealtOut']:
        return f"Commander Secret Santa is rdy to start! {len(decks)-1} are in the giftpool."
    else:
        return f"Registration is still ongoing. {len(decks)-1} decks have been registered yet."

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
    print(tmp_decks)
    return tmp_decks.to_string()

@app.get( '/addAll', status_code=201 )
async def add_all( request: Request):
    add_deck( 1, 'Julian Dürr' )
    add_deck( 2, 'Steven' )
    add_deck( 3, 'Sidney' )
    add_deck( 4, 'Julien' )
    add_deck( 5, 'Daniel' )
    add_deck( 6, 'Basti' )
    # return "6 Decks Added!"
    return templates.TemplateResponse( "thanks.html", {"request": request} )
    
#@app.post( '/thanks', response_class=HTMLResponse )
#def get_thanks(request: Request):
#    return templates.TemplateResponse("thanks.html", {"request": request})
    
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
        return f"Thanks {creator}, your deck is now in the gift pool!"

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
        return "Not all Decks yet Registred! Please wait until the Commander Secret Santa starts."
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