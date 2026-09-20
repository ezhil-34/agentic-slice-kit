export OPENROUTER_API_KEY=your-openrouter-api-key

# Run the live interactive Misconception Tracker web application
uvicorn web.student:app --port 8002

# Open browser at:
# http://localhost:8002
