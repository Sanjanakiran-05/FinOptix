from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.hashers import make_password, check_password
from .models import RegisteredUser, StockLibrary
import os
import pymysql
import io
import base64
import json

# INTERACTIVE GRAPH IMPORTS
import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder

import yfinance as yf
import pandas as pd
from google import genai
from groq import Groq


# --- 1. Global Configuration ---
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
selected_model_name = ""
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def get_stock_data(ticker, period):
    try:
        stock = yf.Ticker(ticker)
        data = stock.history(period=period)
        if data.empty: return None
        return data
    except: return None

def calculate_technical_indicators(data, window=20):
    if data is None: return None
    data['SMA'] = data['Close'].rolling(window=window).mean()
    return data

# --- 2. Dual-AI Interaction ---
def get_investment_suggestion(technical_summary, model_to_use):
    prompt = f"""
    You are a professional financial advisor. Analyze this stock based on the following technical data:
    {technical_summary}

    Provide your response in TWO sections with clear bullet points:
    1. TECHNICAL ANALYSIS: (Explain the SMA and Price trend in bullet points)
    2. STRATEGIC OUTLOOK: (Explain the company's business health and market position)
    
    Finally, provide:
    DECISION: [BUY, SELL, or HOLD]
    """
    try:
        model_map = {
            "gemini-3-flash": "gemini-3-flash-preview",
            "gemini-1.5-flash": "gemini-1.5-flash",
            "gemini-1.5-pro": "gemini-1.5-pro"
        }
        target_model = model_map.get(model_to_use, "gemini-1.5-flash")
        if not target_model.startswith("models/"):
            target_model = f"models/{target_model}"

        response = client.models.generate_content(model=target_model, contents=prompt)
        if response and response.text:
            return response.text.strip()
    except Exception as gemini_err:
        print(f"Gemini failed, trying Groq fallback: {gemini_err}")

    try:
        completion = groq_client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024,
        )
        return completion.choices[0].message.content
    except Exception as groq_err:
        return f"ERROR: Both Gemini and Groq failed. {str(groq_err)}"

# --- 3. View Functions ---

def index(request): return render(request, 'index.html', {})
def UserLogin(request): return render(request, 'UserLogin.html', {})
def Register(request): return render(request, 'Register.html', {})
def Suggestion(request): return render(request, 'Suggestion.html', {})


def Dashboard(request):
    username = request.session.get('username')
    if not username:
        return redirect('UserLogin')

    if request.method == 'POST':
        ticker = request.POST.get('ticker', '').upper().strip()
        action = request.POST.get('action')

        if action == 'add' and ticker:
            StockLibrary.objects.get_or_create(
                username=username,
                ticker=ticker
            )
        elif action == 'delete' and ticker:
            StockLibrary.objects.filter(
                username=username,
                ticker=ticker
            ).delete()

        return redirect('dashboard')

    library_stocks = list(
        StockLibrary.objects.filter(
            username=username
        ).values_list('ticker', flat=True)
    )

    return render(request, 'Dashboard.html', {
        'library_stocks': library_stocks,
        'username': username
    })

def LoadModel(request):
    if request.method == 'POST':
        global selected_model_name
        selected_model_name = request.POST.get('model_name')
        request.session['model_loaded'] = True 
        messages.success(request, f"Success: {selected_model_name} Model is Ready")
        return redirect('Suggestion')
    return render(request, 'UserScreen.html')

def SuggestionAction(request):
    if request.method == 'POST':
        ticker = request.POST.get('t1', False).upper()
        # Fetching 1 year to match your requested scale
        stock_data = get_stock_data(ticker, "1y") 
        
        if stock_data is not None:
            stock_data = calculate_technical_indicators(stock_data)
            latest_day = stock_data.iloc[-1]
            market_stats = {
                'open': round(latest_day['Open'], 2),
                'high': round(latest_day['High'], 2),
                'low': round(latest_day['Low'], 2),
                'volume': f"{int(latest_day['Volume']):,}"
            }

            # --- PLOTLY GRAPH WITH 2 CURVES (Matches your Image) ---
            fig = go.Figure()
            
            # Curve 1: Close Price (Thin Blue Jagged Line)
            fig.add_trace(go.Scatter(
                x=stock_data.index, 
                y=stock_data['Close'], 
                mode='lines', 
                name='Close Price', 
                line=dict(color='#4472c4', width=1.2)
            ))
            
            # Curve 2: 20-Day SMA (Thick Yellow Smooth Line)
            fig.add_trace(go.Scatter(
                x=stock_data.index, 
                y=stock_data['SMA'], 
                mode='lines', 
                name='20-Day SMA', 
                line=dict(color='#edbd44', width=2.5)
            ))
            
            fig.update_layout(
                template='plotly_white', 
                hovermode='x unified', 
                xaxis=dict(showgrid=True, gridcolor='#f0f0f0'),
                yaxis=dict(showgrid=True, gridcolor='#f0f0f0'),
                height=480, 
                margin=dict(l=10, r=10, t=30, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            graph_json = json.dumps(fig, cls=PlotlyJSONEncoder)

            # AI Logic
            current_price = latest_day['Close']
            current_sma = latest_day['SMA']
            price_diff = current_price - current_sma
            percent_diff = (price_diff / current_sma) * 100

            tech_summary = f"Stock: {ticker}\nPrice: {current_price:.2f}\nSMA: {current_sma:.2f}\nDiff: {percent_diff:.2f}%"
            ai_response = get_investment_suggestion(tech_summary, selected_model_name)
            
            if "ERROR" in ai_response.upper():
                data = f"<div class='text-danger'>{ai_response}</div>"
            else:
                clean_text = ai_response.replace('**', '').replace('###', '').replace('##', '').replace('#', '').replace('*', '•')
                parts = clean_text.split('DECISION:')
                recommendation = parts[1].strip() if len(parts) > 1 else "HOLD"
                analysis_body = parts[0]

                tech_formatted = analysis_body.split('STRATEGIC OUTLOOK:')[0].replace('TECHNICAL ANALYSIS:', '').strip().replace('\n', '<br>')
                strat_formatted = analysis_body.split('STRATEGIC OUTLOOK:')[1].strip().replace('\n', '<br>') if 'STRATEGIC OUTLOOK:' in analysis_body else ""

                color_class = "text-success" if "BUY" in recommendation.upper() else "text-danger" if "SELL" in recommendation.upper() else "text-warning"
                data = f"""
                <div class="mb-4">
                    <h6 class="text-primary fw-bold mb-2">Technical Analysis</h6>
                    <p class="small text-muted">{tech_formatted}</p>
                </div>
                <div class="mb-4">
                    <h6 class="text-success fw-bold mb-2">Strategic Outlook</h6>
                    <p class="small text-muted">{strat_formatted}</p>
                </div>
                <div class="card border-0 bg-light text-center py-3">
                    <span class="small text-secondary fw-bold">RECOMMENDATION</span>
                    <h2 class="{color_class} fw-bold m-0">{recommendation}</h2>
                </div>
                """

            return render(request, 'UserScreen.html', {
                'data': data, 
                'graph_json': graph_json, 
                'stats': market_stats, 
                'ticker': ticker,
                'model_name': selected_model_name
            })
        return render(request, 'UserScreen.html', {'data': "Invalid Ticker Symbol."})
    return redirect('LoadModel')

def UserLoginAction(request):
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')

        user = RegisteredUser.objects.filter(
            username=u
        ).first()

        if user and check_password(p, user.password):
            request.session['model_loaded'] = False
            request.session['username'] = u
            return redirect('dashboard')

        return render(request, 'UserLogin.html', {
            'data': 'Login failed'
        })

    return redirect('UserLogin')


def RegisterAction(request):
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        c = request.POST.get('contact')
        e = request.POST.get('email')
        a = request.POST.get('address', '').strip()

        if RegisteredUser.objects.filter(username=u).exists():
            return render(request, 'Register.html', {
                'data': 'Username already exists'
            })

        hashed_password = make_password(p)

        RegisteredUser.objects.create(
            username=u,
            password=hashed_password,
            contact=c,
            email=e,
            address=a
        )

        return render(request, 'UserLogin.html', {
            'data': 'Registration successful. Please login.'
        })

    return redirect('Register')


def StockDataAPI(request):
    ticker = request.GET.get('ticker', 'AAPL').upper()
    period = request.GET.get('period', '1y')
    
    stock_data = get_stock_data(ticker, period)
    if stock_data is None or stock_data.empty:
        return JsonResponse({'error': 'Invalid ticker or no data available.'}, status=400)
    
    stock_data = calculate_technical_indicators(stock_data)
    latest_day = stock_data.iloc[-1]
    
    import math
    # We parse to string to avoid timezone JSON serialization errors
    dates = stock_data.index.strftime('%Y-%m-%d %H:%M:%S').tolist()
    closes = [None if pd.isna(x) else x for x in stock_data['Close']]
    smas = [None if pd.isna(x) else x for x in stock_data['SMA']]
    
    market_stats = {
        'open': round(latest_day['Open'], 2),
        'high': round(latest_day['High'], 2),
        'low': round(latest_day['Low'], 2),
        'volume': f"{int(latest_day['Volume']):,}",
        'close': round(latest_day['Close'], 2)
    }

    return JsonResponse({
        'status': 'success',
        'dates': dates,
        'closes': closes,
        'smas': smas,
        'stats': market_stats
    })


def DemoDashboard(request):
    demo_stocks = [
        {'ticker': 'AAPL', 'company': 'Apple Inc.', 'price': '$273.50', 'change': '+1.25%'},
        {'ticker': 'MSFT', 'company': 'Microsoft', 'price': '$505.20', 'change': '+0.82%'},
        {'ticker': 'NVDA', 'company': 'NVIDIA', 'price': '$186.40', 'change': '+2.14%'},
        {'ticker': 'GOOGL', 'company': 'Alphabet', 'price': '$295.10', 'change': '-0.35%'},
    ]

    return render(request, 'DemoDashboard.html', {
        'demo_stocks': demo_stocks
    })

