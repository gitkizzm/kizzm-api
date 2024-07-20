1# -*- coding: utf-8 -*-
"""
Created on Wed Nov 15 22:14:26 2023

@author: xbox

https://www.youtube.com/watch?v=0TFWtfFY87U
https://www.back4app.com/docs-containers/deployment-process

form tinkering
    https://github.com/itsthatianguy/youtube/blob/main/fastapi-forms-file-upload/app.py
    https://www.youtube.com/watch?v=L4WBFRQB7Lk
    
    
responsive html stuff
https://www.w3schools.com/html/tryit.asp?filename=tryhtml_responsive_media_query

infos zu async
https://fastapi.tiangolo.com/async/

htmx
https://www.youtube.com/watch?v=yu0TbJ2BQso

URL Rerouting
https://stackoverflow.com/questions/75726959/how-to-reroute-requests-to-a-different-url-endpoint-in-fastapi

general FastAPI Tutorial:
https://www.youtube.com/playlist?list=PLqAmigZvYxIL9dnYeZEhMoHcoP4zop8-p
"""

import uvicorn
from fastapi import FastAPI, Request, Form, Depends, UploadFile, File, Query, Header
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from schemas import AwesomeForm, RegForm
from typing import Optional
from random import shuffle
from pandas import DataFrame, read_json
import os
import random

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
def clear_json( request: Request ):
    new_deck = DataFrame( [ { "id": 0,
                               "creator": "",
                               "owner": "",
                                "dealtOut": False
                               } ] )
    new_deck = new_deck.set_index( 'id' )
    new_deck.to_json( 'raffle.json' )
    response = {}
    response['title'] = 'Retry'
    response['str'] = "Commander Secret Santa restarted. All Data cleared!"
    context = { 'request': request, 'response': response }
    return templates.TemplateResponse( "response.html", context )
    
@app.get( '/', status_code=200 )
def get_status( request: Request, hx_request: Optional[str] = Header(None) ):
    decks = read_json( 'raffle.json' )
    response = {}
    if decks.at[0,'dealtOut']:
        response['title'] = 'Raffle Time!!'
        response['str'] = f"Commander Secret Santa is rdy to start! {len(decks)-1} decks are in the giftpool."
    else:
        response['title'] = 'Checkin ongoing'
        response['str'] = f"Registration is still ongoing. {len(decks)-1} decks have been registered yet."
    context = { 'request': request, 'response': response }
    #if hx_request:
    #    return templates.TemplateResponse("partials/table.html", context)
    return templates.TemplateResponse( "response.html", context )

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
async def add_all( request: Request ):
    creator_pool = [ 'Steven', 'Sidney', 'Basti', 'Pepe/Phillip']
    random.shuffle( creator_pool )

    for i in range( len(creator_pool) ):
        add_deck( i+1, creator_pool[i] )

    response = {}
    response['title'] = 'Ready'
    response['str'] = "All 4 Decks where added"
    context = { 'request': request, 'response': response }
    return templates.TemplateResponse( "response.html", context )
    # return templates.TemplateResponse( "thanks.html", {"request": request} )

# @app.get( '/', status_code=201 )
# def check_id( d_id: int = Query( None, title='DID', description='The Deckid from QR-Code' ) ):
#     decks = read_json( 'raffle.json' )
#     if decks.at[0,'dealtOut']:
#         return "Registration closed. Decks get dealtout now!"
#     if d_id in decks.index.values:
#         return "This deck is already registred!"
#     else:
#         decks.at[ d_id, "creator" ] = creator
#         decks.at[ d_id, "owner" ] = ""
#         decks.at[ d_id, "dealtOut" ] = False
#         decks.to_json( 'raffle.json' )
#         return f"Thanks {creator}, your deck is now in the gift pool!"
#     return d_id

# @app.get( '/reg/{d_id}', status_code=201 )
# def get_tst_P( request: Request, d_id : int, creator: str = Form(...) ):
    
#     # print( f'creator: {creator}')

#     decks = read_json( 'raffle.json' )
#     response = {}

#     if not d_id:      
#         if decks.at[0,'dealtOut']:
#             response['title'] = 'Start'
#             response['str'] = f'Registration closed. Decks get dealtout now!'
#             context = { 'request': request, 'response': response }
#             return templates.TemplateResponse( "partials/status.html", context )
#         else:
#             response['title'] = 'Waiting'
#             response['str'] = f'Registration sill ongoin!'
#             context = { 'request': request, 'response': response }
#             return templates.TemplateResponse( "partials/status.html", context )
    
    

#     if d_id in decks.index.values:
#         if decks.at[0,'dealtOut']:
#             response['title'] = 'Start'
#             response['str'] = f'Registration closed. Decks get dealtout now!'
#             context = { 'request': request, 'response': response }
#             return templates.TemplateResponse( "partials/status.html", context )
#         else:
#             response['title'] = 'Waiting'
#             response['str'] = f'Your Deck was registred, waiting for the raffle to start!'
#             context = { 'request': request, 'response': response }
#             return templates.TemplateResponse( "partials/status.html", context )
#     else:
#         response['title'] = 'Onboarding'
#         response['str'] = f""

#         context = { 'request': request, 'response': response }
#         return templates.TemplateResponse( "onboarding.html", context )

# @app.post( '/reg/{d_id}' )
# def post_tst_P( request: Request,  d_id : int, creator: str = Form(...) ):

#     # print( f'creator: {creator}')

#     decks = read_json( 'raffle.json' )
#     response = {}

#     if not d_id:      
#         if decks.at[0,'dealtOut']:
#             response['title'] = 'Start'
#             response['str'] = f'Registration closed. Decks get dealtout now!'
#             context = { 'request': request, 'response': response }
#             return templates.TemplateResponse( "partials/status.html", context )
#         else:
#             response['title'] = 'Waiting'
#             response['str'] = f'Registration sill ongoin!'
#             context = { 'request': request, 'response': response }
#             return templates.TemplateResponse( "partials/status.html", context )
    
    

#     if d_id in decks.index.values:
#         if decks.at[0,'dealtOut']:
#             response['title'] = 'Start'
#             response['str'] = f'Registration closed. Decks get dealtout now!'
#             context = { 'request': request, 'response': response }
#             return templates.TemplateResponse( "partials/status.html", context )
#         else:
#             response['title'] = 'Waiting'
#             response['str'] = f'Your Deck was registred, waiting for the raffle to start!'
#             context = { 'request': request, 'response': response }
#             return templates.TemplateResponse( "partials/status.html", context )
#     else:
#         response['title'] = 'Onboarding'
#         response['str'] = f""

#         context = { 'request': request, 'response': response }
#         return templates.TemplateResponse( "onboarding.html", context )
# @app.post( '/', status_code=201 )
# def post_tst_P( request: Request, form_data: RegForm = Depends(RegForm.as_form)):
    
#         decks.at[ d_id, "creator" ] = creator
#         decks.at[ d_id, "owner" ] = ""
#         decks.at[ d_id, "dealtOut" ] = False
#         decks.to_json( 'raffle.json' )
#     d_id = 1
#     creator = 'Basti'

#     response = {}
#     response['title'] = 'Onboarding'

#     decks = read_json( 'raffle.json' )
#     if decks.at[0,'dealtOut']:
#         response['str'] = f'Registration closed. Decks get dealtout now!'
#         context = { 'request': request, 'response': response }
#         return templates.TemplateResponse( "onboarding.html", context )
#     elif d_id in decks.index.values:
#         response['str'] = f'Sry, your Deck has allready been registred.'
#         context = { 'request': request, 'response': response }
#         return templates.TemplateResponse( "onboarding.html", context )
#     else:
#         decks.at[ d_id, "creator" ] = creator
#         decks.at[ d_id, "owner" ] = ""
#         decks.at[ d_id, "dealtOut" ] = False
#         decks.to_json( 'raffle.json' )
#         response['str'] = f"Thanks {creator}! Your registered Deck {d_id}. Please wait for the raffle to start."
#         context = { 'request': request, 'response': response }
#         return templates.TemplateResponse( "response.html", context )
    
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
def start_raffle( request: Request ):
    # manually starts the raffle, this stops registration access and shuffles
    decks = read_json( 'raffle.json' )
    if len(decks) < 3:
        response = {}
        response['title'] = 'Checkin ongoing'
        response['str'] = f"Only {len(decks)-1} decks are registred yet. Cant start the Raffle"
        context = { 'request': request, 'response': response }
        return templates.TemplateResponse( "response.html", context ) 
  
    decks.at[0,'dealtOut'] = True #newRaffleRdy Bool
    gOrder, cOrder = shuffle_decks( decks )
    
    for gifted, gifter in zip( gOrder, cOrder ):
        decks.at[ gifter, 'owner' ] = decks.at[ gifted, 'creator' ]
    decks.to_json( 'raffle.json' )
    return get_status( request ) 
                
@app.get( '/deal', status_code=201 )
def dealout_deck( request: Request, d_id: int = Query( None, title='DID', description='The Deckid from QR-Code' ) ):
    decks = read_json( 'raffle.json' )
    response = {}

    if not decks.at[0,'dealtOut']:
        response['title'] = 'Registration'
        creator = decks.at[d_id,'creator']
        response['str'] = f'Pleas hand this box over to {creator}'
        context = { 'request': request, 'response': response }
        return templates.TemplateResponse( "partials/status.html", context )
    else:
        decks.at[d_id,'dealtOut'] = True
        decks.to_json( 'raffle.json' )
        name = decks.at[d_id,'owner']
        response['title'] = 'Start'
        response['str'] = f'Please hand this deck over to {name}!'
        context = { 'request': request, 'response': response }
        return templates.TemplateResponse( "partials/status.html", context )

if __name__ == "__main__":
    new_deck = DataFrame( [ { "id": 0,
                               "creator": "",
                               "owner": "",
                               "dealtOut": False
                               } ] )
    new_deck = new_deck.set_index( 'id' )
    new_deck.to_json( 'raffle.json' )
    del new_deck
    uvicorn.run('main:app', port=8080, host="0.0.0.0", reload=True)