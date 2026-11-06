$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
function Send-Result($kind, $message) {
    @{ kind = $kind; message = $message } | ConvertTo-Json -Compress | ForEach-Object { [Console]::WriteLine($_) }
}
try {
    $voice = New-Object -ComObject SAPI.SpVoice
    $selected = $false
    # Use installed OneCore Chinese voices without changing the system registry.
    $tokens = Get-ChildItem -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens' -ErrorAction SilentlyContinue
    foreach ($entry in $tokens) {
        $attributes = Get-ItemProperty -LiteralPath ($entry.PSPath + '\Attributes') -ErrorAction SilentlyContinue
        if ($attributes.Language -match '(^|;)804(;|$)') {
            try {
                $token = New-Object -ComObject SAPI.SpObjectToken
                $token.SetId(('HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens\' + $entry.PSChildName), '', $false)
                $voice.Voice = $token
                $selected = $true
                break
            } catch { }
        }
    }
    if (-not $selected) {
        foreach ($token in $voice.GetVoices()) {
            if ($token.GetAttribute('Language') -match '(^|;)804(;|$)') {
                $voice.Voice = $token
                $selected = $true
                break
            }
        }
    }
    if (-not $selected) { throw 'No installed Simplified Chinese speech voice was found.' }
    Send-Result 'speech_ready' ($voice.Voice.GetDescription())
    while ($null -ne ($line = [Console]::ReadLine())) {
        try {
            $request = $line | ConvertFrom-Json
            if ($request.command -eq 'stop') { break }
            $voice.Rate = [Math]::Max(-5, [Math]::Min(5, [int]$request.rate))
            $voice.Volume = 90
            $stream = $null
            $originalOutput = $voice.AudioOutput
            try {
                if ($request.file) {
                    $stream = New-Object -ComObject SAPI.SpFileStream
                    $stream.Open([string]$request.file, 3, $false)
                    $voice.AudioOutputStream = $stream
                }
                [void]$voice.Speak([string]$request.text, 0)
                Send-Result 'speech_done' 'ok'
            } finally {
                if ($stream) {
                    $stream.Close()
                    $voice.AudioOutput = $originalOutput
                }
            }
        } catch { Send-Result 'speech_error' $_.Exception.Message }
    }
} catch {
    Send-Result 'speech_error' $_.Exception.Message
    exit 1
}
