<#
.SYNOPSIS
  Signe les fichiers d'une version d'AxiorHub Pilote avant de les déposer sur la page des versions GitHub (5.6.61).

.DESCRIPTION
  Produit, à côté de chaque fichier, sa signature « <fichier>.sig » (format SSHSIG d'OpenSSH, espace de noms axiorhub-release) :
    axiorhub-mail-agent-<version>.tar.gz          (serveur Linux)
    AxiorHub-Pilote-Setup-<version>.exe           (Windows 11)
    axiorhub-pilote-poste_<version>_amd64.deb     (Ubuntu poste)
    axiorhub-pilote_<version>_all.deb             (paquet serveur, s'il est présent)
  La clé privée reste sur ce poste ; ssh-keygen demande sa phrase de passe une fois par fichier (ou une seule fois si
  l'agent SSH de Windows la garde). Chaque signature est vérifiée aussitôt avec la clé publique.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\signer-version.ps1 -Version 5.6.61 -Dossier C:\livraison
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Dossier,
    [string]$Cle = (Join-Path $env:USERPROFILE '.ssh\axiorhub_release')
)
$ErrorActionPreference = 'Stop'
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version invalide : $Version" }
if (-not (Test-Path $Cle)) { throw "Clé privée introuvable : $Cle" }
if (-not (Test-Path "$Cle.pub")) { throw "Clé publique introuvable : $Cle.pub" }
$noms = @("axiorhub-mail-agent-$Version.tar.gz", "AxiorHub-Pilote-Setup-$Version.exe",
          "axiorhub-pilote-poste_$($Version)_amd64.deb", "axiorhub-pilote_$($Version)_all.deb")
$signataires = Join-Path $env:TEMP "axiorhub-signataires-$PID"
$publique = (Get-Content "$Cle.pub" -Raw).Trim().Split(' ')
Set-Content -Path $signataires -Value ('axiorhub-pilote namespaces="axiorhub-release" ' + $publique[0] + ' ' + $publique[1]) -Encoding ascii
$faits = 0
try {
    foreach ($nom in $noms) {
        $f = Join-Path $Dossier $nom
        if (-not (Test-Path $f)) { Write-Host "absent (ignoré) : $nom"; continue }
        if (Test-Path "$f.sig") { Remove-Item "$f.sig" }
        & ssh-keygen -Y sign -f $Cle -n axiorhub-release $f
        if ($LASTEXITCODE -ne 0) { throw "Signature refusée : $nom" }
        cmd /d /c "ssh-keygen -Y verify -f `"$signataires`" -I axiorhub-pilote -n axiorhub-release -s `"$f.sig`" < `"$f`""
        if ($LASTEXITCODE -ne 0) { throw "Vérification échouée : $nom" }
        Write-Host "signé et vérifié : $nom.sig"
        $faits++
    }
} finally {
    Remove-Item $signataires -ErrorAction SilentlyContinue
}
if ($faits -eq 0) { throw "Aucun fichier de la version $Version dans $Dossier" }
Write-Host "$faits signature(s). Déposer les fichiers .sig sur la page de la version v$Version, à côté des fichiers signés."
