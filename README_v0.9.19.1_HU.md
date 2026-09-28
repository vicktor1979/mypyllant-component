# mypyllant-component v0.9.19.1 – rendszerenkénti gateway hibakezelés

Ez a verzió a `signalkraft/mypyllant-component` **v0.9.19** kiadására épülő fork-specifikus javítás.

- Upstream: `signalkraft/mypyllant-component` v0.9.19
- Fork: `vicktor1979/mypyllant-component`
- Fork verzió: **v0.9.19.1**
- Cél: több myVAILLANT rendszer használatakor egyetlen hibás/elérhetetlen gateway ne tegye elérhetetlenné az összes többi rendszert is.

## A probléma

Ha ugyanahhoz a myVAILLANT fiókhoz több rendszer / telephely / gateway tartozik, a komponens eredeti működése egy közös `SystemCoordinator` frissítést használ.

A rendszerlista egyetlen `get_systems()` iterációval került lekérésre. Ha például 5 rendszerből a harmadik gateway lekérése `503`, `504`, timeout vagy egyéb rendszer-specifikus hibával megszakadt, a teljes frissítés sikertelen lett.

Ennek következménye:

```text
Rendszer 1 -> OK
Rendszer 2 -> OK
Rendszer 3 -> 504 Gateway Time-out
Rendszer 4 -> valójában OK
Rendszer 5 -> valójában OK

Eredeti eredmény:
MIND az 5 rendszer entitásai unavailable állapotba kerülhettek.
```

Ez megnehezítette annak megállapítását is, hogy valójában melyik gateway okozza a problémát.

## A javítás működése

### 1. Rendszerenként elkülönített lekérés

A `SystemCoordinator` most minden `Home` / rendszer adatait külön kéri le:

```text
Rendszer 1 -> külön lekérés -> OK
Rendszer 2 -> külön lekérés -> OK
Rendszer 3 -> külön lekérés -> 504
Rendszer 4 -> külön lekérés -> OK
Rendszer 5 -> külön lekérés -> OK
```

Egy rendszer hibája így nem szakítja meg a többi rendszer frissítését.

### 2. Rendszerenkénti availability

A coordinator rendszerenként tartja nyilván a legutóbbi frissítés eredményét.

Új logika:

```python
coordinator.is_system_available(system_id)
```

Az adott rendszerhez tartozó entitások csak akkor lesznek elérhetetlenek, ha **annak a rendszernek** a frissítése hibázott.

Várt eredmény:

```text
Rendszer 1 -> Available
Rendszer 2 -> Available
Rendszer 3 -> Unavailable
Rendszer 4 -> Available
Rendszer 5 -> Available
```

### 3. Utolsó jó adatok megtartása

Ha egy korábban sikeresen lekért rendszer átmenetileg elérhetetlenné válik, az utolsó ismert `System` objektum megmarad.

Ez két okból fontos:

- a meglévő entity-k nem veszítik el a rendszerükhöz tartozó hivatkozást;
- a rendszerlista indexei nem csúsznak el.

A hibás rendszer entitásai ettől még `unavailable` állapotúak lesznek, tehát a régi érték nem jelenik meg friss adatként.

### 4. Stabil rendszer/index hozzárendelés

Az integráció több helyen `system_index` alapján éri el az adatokat.

Ha egy hibás rendszert egyszerűen eltávolítanánk a listából, az utána következő entity-k más rendszer adataira mutathatnának.

A javítás ezért megtartja a meglévő rendszerek sorrendjét, és rendszerazonosító (`system.id`) alapján kezeli őket.

### 5. Új diagnosztikai binary sensor gatewayenként

Minden myVAILLANT Home/gateway kap egy új diagnosztikai szenzort:

```text
<telephely neve> Gateway API Connection
```

Például:

```text
Honvéd u. 41 Gateway API Connection
```

Device class:

```text
connectivity
```

A szenzor állapota:

- `on` = a rendszer legutóbbi API frissítése sikeres volt;
- `off` = az adott rendszer frissítése hibázott.

A diagnosztikai attribútumok:

```text
system_id
last_success
last_error
last_error_type
last_error_at
last_http_status
last_error_url
gateway_online_state
```

Példa:

```yaml
last_http_status: 504
last_error_type: ClientResponseError
last_error: "504, message='Gateway Time-out'"
last_error_url: "https://api.vaillant-group.com/.../currentSystem"
```

Így közvetlenül Home Assistantból látható, hogy melyik telephely/gateway okozza a hibát.

### 6. DailyDataCoordinator izoláció

Az energia- és napi adatok frissítése is rendszerenként lett elkülönítve.

Ha egy rendszer hibás:

- a többi rendszer daily/energy adatai tovább frissülnek;
- a hibás rendszer korábbi daily adatai megmaradnak;
- ha a fő `SystemCoordinator` már hibásnak jelölte a rendszert, a `DailyDataCoordinator` nem terheli azt további felesleges API-hívásokkal.

### 7. Globális quota hibák továbbra is globálisak

A Vaillant API quota / rate-limit kezelését a módosítás nem próbálja rendszerenként elszigetelni.

Ez szándékos, mert a quota jellemzően fiók/API szintű állapot, ezért az eredeti backoff mechanizmus megmarad.

### 8. Coordinator state példányszintűvé tétele

A `homes` lista korábban class attribute volt:

```python
homes: list[Home] = []
```

A javítás ezt példányszintű állapottá teszi:

```python
self.homes: list[Home] = []
```

Ez csökkenti annak kockázatát, hogy több config entry között állapot szivárogjon át.

### 9. Python exception szintaxis javítások

Két régi exception szintaxis javítva lett:

```python
except ValueError, TypeError:
```

helyett:

```python
except (ValueError, TypeError):
```

és:

```python
except ValueError, AttributeError:
```

helyett:

```python
except (ValueError, AttributeError):
```

## Módosított fájlok

| Fájl | Módosítás |
|---|---|
| `custom_components/mypyllant/coordinator.py` | Rendszerenkénti API hibakezelés, failure state, last-success, DailyData izoláció |
| `custom_components/mypyllant/binary_sensor.py` | Új `Gateway API Connection` diagnosztikai entity + rendszer availability |
| `custom_components/mypyllant/utils.py` | Közös entity availability és exception szintaxis javítás |
| `custom_components/mypyllant/calendar.py` | Exception szintaxis javítás |
| `custom_components/mypyllant/climate.py` | Rendszerenkénti availability |
| `custom_components/mypyllant/number.py` | Meglévő availability feltételek összevonása a coordinator availability-vel |
| `custom_components/mypyllant/sensor.py` | Rendszerenkénti availability több sensor alaposztályban |
| `custom_components/mypyllant/switch.py` | Rendszerenkénti availability |
| `custom_components/mypyllant/ventilation_climate.py` | Rendszerenkénti availability |
| `custom_components/mypyllant/water_heater.py` | Rendszerenkénti availability |
| `custom_components/mypyllant/manifest.json` | Fork verzió: `v0.9.19.1` |
| `pyproject.toml` | Fork verzió: `0.9.19.1` |
| `uv.lock` | Fork package verzió frissítése |

## Verziózási szabály a forkban

A fork verziója az upstream verzióhoz egy negyedik patch számot ad:

```text
upstream v0.9.19
fork     v0.9.19.1
```

További saját javítások ugyanazon upstream alapon:

```text
v0.9.19.2
v0.9.19.3
...
```

Ha az upstream például `v0.9.20` verzióra frissül, az első saját változat:

```text
v0.9.20.1
```

## Telepítés / tesztelés

1. Készíts biztonsági mentést a jelenlegi `custom_components/mypyllant` könyvtárról.
2. Másold be a módosított fájlokat az azonos útvonalakra.
3. Indítsd újra a Home Assistantot.
4. Ellenőrizd, hogy minden Home/gateway alatt megjelent-e a `Gateway API Connection` diagnosztikai entity.
5. Teszteld egy ismerten elérhetetlen gatewayjel.
6. Ellenőrizd, hogy csak az adott rendszer entity-jei válnak `unavailable` állapotúvá.
7. Ellenőrizd, hogy a többi rendszer értékei tovább frissülnek.
8. Ellenőrizd a problémás gateway diagnosztikai entity attribútumait.

## Debug log

Teszteléshez az upstream dokumentáció szerinti logging használható:

```yaml
logger:
  default: warning
  logs:
    custom_components.mypyllant: debug
    myPyllant: debug
```

**Figyelem:** GitHubra feltöltés előtt minden logból töröld/maszkold az email-címet, jelszót, tokent, system UUID-ket, sorozatszámokat, címeket és más személyes adatokat.

## Ellenőrzések

A v0.9.19.1 patchen elvégzett statikus ellenőrzések:

- Python `compileall`: sikeres
- `manifest.json` parse: sikeres
- `pyproject.toml` parse: sikeres

A valódi több-gatewayes Vaillant API működés végső ellenőrzéséhez Home Assistant runtime teszt szükséges.

## Ismert szélső eset

Ha egy gateway **már az integráció első betöltésekor** sem ad vissza használható `System` objektumot, a gateway diagnosztikai entity létrehozható, de a rendszer normál entity-jeihez nincs még modelladat.

Ha a gateway később helyreáll, az integráció újratöltése szükséges lehet ahhoz, hogy az adott rendszer teljes entity-készlete létrejöjjön.

## Upstream javaslat

A funkcionális javítás upstream Pull Requestként is beküldhető a `signalkraft/mypyllant-component` projektbe.

Upstream PR esetén a fork-specifikus verziómódosításokat (`v0.9.19.1`) érdemes kihagyni, és csak a tényleges hibajavítást beküldeni.
