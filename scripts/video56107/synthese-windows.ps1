# 5.6.107 (lot V) : voix de synthese locale (System.Speech) pour la video de demonstration ; aucun service en ligne.
# Fichier en ASCII seulement : PowerShell 5.1 lit un script sans BOM dans la page de code ANSI.
# Lit une demande JSON (UTF-8) : travaux [{id, texte, wav}], voix preferees, culture, debit, volume, frequence ; ecrit un WAV PCM 16 bits
# mono par travail, puis un resultat JSON {voix} avec le nom de la voix retenue. Erreur si aucune voix de la culture n'est installee.
param([Parameter(Mandatory = $true)][string]$Demande, [Parameter(Mandatory = $true)][string]$Resultat)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$d = Get-Content -Raw -Encoding UTF8 -LiteralPath $Demande | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$installees = @($synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo })
$choisie = $null
foreach ($nom in $d.preferees) {
    $choisie = $installees | Where-Object { $_.Name -like ('*' + $nom + '*') -and $_.Culture.Name -eq $d.culture } | Select-Object -First 1
    if ($null -ne $choisie) { break }
}
if ($null -eq $choisie) { $choisie = $installees | Where-Object { $_.Culture.Name -eq $d.culture } | Select-Object -First 1 }
if ($null -eq $choisie) {
    throw ('Aucune voix ' + $d.culture + ' installee sur ce poste.')
}
$synth.SelectVoice($choisie.Name)
$synth.Rate = [int]$d.debit
$synth.Volume = [int]$d.volume
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo([int]$d.frequence, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($t in $d.travaux) {
    $synth.SetOutputToWaveFile($t.wav, $format)
    $synth.Speak([string]$t.texte)
    $synth.SetOutputToNull()
}
$synth.Dispose()
$sortie = @{ voix = ($choisie.Name + ' (' + $choisie.Culture.Name + ')') } | ConvertTo-Json -Compress
[System.IO.File]::WriteAllText($Resultat, $sortie, (New-Object System.Text.UTF8Encoding($false)))
