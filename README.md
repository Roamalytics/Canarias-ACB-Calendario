# Canarias ACB Calendario

Calendarios .ics de La Laguna Tenerife (CB Canarias), actualizados cada día de forma automática.

## Suscribirse en Google Calendar

Google Calendar > `+` junto a "Otros calendarios" > **Desde URL** > pega una de estas:

| Calendario | URL |
|---|---|
| Todo (ACB + EuroCup + amistosos + playoffs) | `https://raw.githubusercontent.com/Roamalytics/Canarias-ACB-Calendario/main/canarias-basketball-acb-calendar.ics` |
| Solo ACB | `https://raw.githubusercontent.com/Roamalytics/Canarias-ACB-Calendario/main/acb_calendar_tfe_fixtures.ics` |
| Solo EuroCup | `https://raw.githubusercontent.com/Roamalytics/Canarias-ACB-Calendario/main/eurocup_calendar_tfe_fixtures.ics` |
| Amistosos y playoffs | `https://raw.githubusercontent.com/Roamalytics/Canarias-ACB-Calendario/main/extras_calendar_tfe_fixtures.ics` |

Google refresca las suscripciones cada 12-24 h. El color del calendario se elige en Google (el .ics no puede fijarlo); Apple Calendar sí toma el amarillo aurinegro del fichero.

Todos los horarios son **hora canaria**. Eventos: `ACB: Canarias - Rival`, `EC: Canarias - Rival`, `Amistoso: ...`, `Playoff ACB: ...`.

## Cómo funciona

Cada día a las 05:17 UTC, GitHub Actions ejecuta `update_calendar.py`:

1. Lee la tabla de partidos de [cbcanarias.net/temporada](https://cbcanarias.net/temporada/) (ACB + EuroCup).
2. Lee `extra_fixtures.csv` (amistosos, placeholders de playoff, lo que no esté en la web del club).
3. Para los partidos de los próximos 15 días comprueba fecha y hora contra la fuente oficial (PDF del calendario ACB, API de Euroleague). Si difieren, manda la liga y se abre un issue en el repo.
4. Fusiona todo en los .ics por UID: añade nuevos, actualiza cambios, **nunca borra**. Si el club retira un partido de su web, sigue en el calendario.
5. Hace commit solo si algún fichero cambió.

Si la web del club devuelve menos de 30 partidos, el script se detiene sin tocar nada y se abre un issue.

## Añadir un partido a mano

Añade una línea a `extra_fixtures.csv`:

```
competition,date,time_canary,home,away,venue,round,allday,url
Amistoso,2026-09-20,12:00,Canarias,Surne Bilbao,"Pabellón Santiago Martín, La Laguna",Copa Tenerife,,
```

- `time_canary` vacío = evento sin hora ("hora por confirmar")
- `allday=1` = evento de día completo (placeholders)
- `date=TBC` = la fila se ignora hasta que tenga fecha

## Probar en local

```
pip install -r requirements.txt
python update_calendar.py --dry-run                                   # contra la web real
python update_calendar.py --html=tests/club_page_2026-09-15.html --no-crosscheck   # sin red
```
