# Construye la imagen de Docker Compose declarando el commit de Git del código que se empaqueta.
#
#   .\scripts\docker_build.ps1                 construye todas las imágenes
#   .\scripts\docker_build.ps1 --no-cache      pasa argumentos adicionales a `docker compose build`
#
# La imagen no contiene .git: solo recibe el identificador del commit (GIT_COMMIT) y si el árbol tenía cambios
# sin confirmar de archivos versionados (GIT_TREE_MODIFIED). Sin un commit verificable no se declara ninguno.
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $BuildArguments)

Set-Location (Split-Path -Parent $PSScriptRoot)

$commit = ""
$modified = ""
$head = (git rev-parse HEAD) 2>$null
if ($LASTEXITCODE -eq 0 -and $head -match '^[0-9a-f]{40}$') {
    $commit = $head
    $changes = git status --porcelain --untracked-files=no
    $modified = if ($changes) { "true" } else { "false" }
}

if ($commit) {
    Write-Host "Commit declarado: $commit (cambios sin confirmar: $modified)"
} else {
    Write-Warning "No hay un commit de Git verificable; la imagen registrará el commit como no disponible."
}

$env:GIT_COMMIT = $commit
$env:GIT_TREE_MODIFIED = $modified
docker compose build @BuildArguments
exit $LASTEXITCODE
