Write-Host "Starting EasyEcom AI Assistant..."

$SCRIPT_DIR = $PSScriptRoot
$VENV_PYTHON = Join-Path $SCRIPT_DIR ".venv\Scripts\python.exe"

if (!(Test-Path $VENV_PYTHON)) {
    Write-Host "❌ venv python not found at $VENV_PYTHON"
    pause
    exit
}

Write-Host "Starting Mock API server..."
Start-Process powershell `
  -WorkingDirectory $SCRIPT_DIR `
  -ArgumentList "-NoExit","-Command","& '$VENV_PYTHON' -m uvicorn mock_apis.easyecom_mock:mock_app --host 0.0.0.0 --port 8001"

Start-Sleep 3

Write-Host "Starting Main API server..."
Start-Process powershell `
  -WorkingDirectory $SCRIPT_DIR `
  -ArgumentList "-NoExit","-Command","& '$VENV_PYTHON' main.py"

Start-Sleep 3

Write-Host "Starting Streamlit UI..."
Start-Process powershell `
  -WorkingDirectory $SCRIPT_DIR `
  -ArgumentList "-NoExit","-Command","& '$VENV_PYTHON' -m streamlit run streamlit_ui.py"

Write-Host ""
Write-Host "All services started!"
Write-Host "- Mock API: http://localhost:8001"
Write-Host "- Main API: http://localhost:8000"
Write-Host "- Streamlit UI: http://localhost:8501"