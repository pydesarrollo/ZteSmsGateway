# Arranque automático al iniciar Windows

Ya existen en la raíz del proyecto. Usan la carpeta donde estén guardados, así que funcionan
sin editar en cualquier ruta (ej. `C:\Apps\ZteSmsGateway` en el servidor):

`iniciar_api.bat`:
```bat
@echo off
cd /d "%~dp0"
python -m uvicorn api:app --host 0.0.0.0 --port 8000 >> log.txt 2>&1
```

`iniciar_api_oculto.vbs` (lanza el `.bat` sin mostrar ventana de consola):
```vbscript
Set fso = CreateObject("Scripting.FileSystemObject")
carpeta = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run """" & carpeta & "\iniciar_api.bat""", 0, False
```

> El `.vbs` es necesario porque `schtasks /sc onlogon` sin credenciales
> guardadas corre en modo interactivo: si apuntas la tarea directo al `.bat`,
> se abre una ventana de `cmd` visible y **cerrarla mata el proceso**. Apuntando
> al `.vbs`, `wscript` lanza el `.bat` con ventana oculta (estilo `0`), así que
> no hay ventana que alguien pueda cerrar por accidente.

## Registrar la tarea

Abrir **PowerShell como Administrador** y ejecutar:

```powershell
schtasks /create /tn "SMS Gateway API" /tr "C:\Apps\ZteSmsGateway\iniciar_api_oculto.vbs" /sc onlogon /rl highest /f
```

(apuntar directo al `.vbs` alcanza — Windows lo asocia con `wscript.exe` solo.
No metas `wscript.exe "..."` a mano en PowerShell: las comillas anidadas con
`\"` no se escapan igual que en cmd y `schtasks` recibe el argumento roto.)

Esto crea una tarea que arranca la API (oculta) apenas esa cuenta de Windows
inicia sesión.

Si ya habías creado la tarea apuntando directo al `.bat`, hay que borrarla y
recrearla con el comando de arriba (o correr ese mismo comando con `/f`, que
sobrescribe la tarea existente).

> Para que quede listo sin depender de que alguien desbloquee la PC, configurar
> **inicio de sesión automático** de esa cuenta (o dejar la sesión siempre
> iniciada y solo bloqueada, nunca cerrada).

## Probar

Antes de probar, cerrar cualquier `uvicorn` corriendo a mano en el puerto 8000
(si no, la tarea fallará por puerto ocupado):

```powershell
schtasks /run /tn "SMS Gateway API"
```

Luego entrar a `http://localhost:8000/` — debería responder. También se puede
reiniciar la PC y verificar que arranca solo al llegar al escritorio.

## Ver logs / errores

La salida (incluidos errores) queda en `log.txt`, en la raíz del proyecto.

## Detener el proceso que quedó corriendo

Si la tarea ya arrancó la API y quieres pararla (sin desactivar la tarea, o
sea que puede volver a arrancar en el próximo logon):

```powershell
schtasks /end /tn "SMS Gateway API"
```

Si eso no la corta (por ejemplo porque la iniciaste a mano, no por la tarea),
mata el proceso que tiene el puerto 8000 abierto:

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

## Si cambias el puerto (u otra cosa del `.bat`)

Editar `iniciar_api.bat` no afecta al proceso que ya está corriendo — sigue
escuchando en el puerto viejo hasta que lo mates. `schtasks /delete` tampoco
lo mata, solo borra el registro de la tarea. Pasos:

1. Editar `iniciar_api.bat` con el cambio.
2. Matar el proceso viejo (ajustar el puerto al que tenía antes):
   ```powershell
   Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
   ```
3. Recrear la tarea y correrla (usa el `.bat` actualizado):
   ```powershell
   schtasks /create /tn "SMS Gateway API" /tr "C:\Apps\ZteSmsGateway\iniciar_api_oculto.vbs" /sc onlogon /rl highest /f
   schtasks /run /tn "SMS Gateway API"
   ```

## Desactivar / quitar la tarea

```powershell
schtasks /delete /tn "SMS Gateway API" /f
```

Esto solo quita el registro en el Programador de tareas — no mata un proceso
que ya esté corriendo (usar la sección anterior para eso).

## Alternativa más robusta (opcional)

`schtasks /sc onlogon` depende de que haya una sesión de Windows iniciada. Si
más adelante se necesita que la API se reinicie sola si se cae, sin depender
de una sesión abierta, correrla como **servicio de Windows** con una
herramienta como [`nssm`](https://nssm.cc/) — es un paso aparte, no cubierto
aquí.
