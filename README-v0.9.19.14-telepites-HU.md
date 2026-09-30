# mypyllant v0.9.19.14 – a közös frissítési sor eltávolítása

## Alap és cél

Upstream alap: signalkraft v0.9.19. A kiadás a beszélgetésben kiadott
**v0.9.19.12 fiókonkénti frissítési működését** állítja vissza, miközben
megtartja a .13 energiaelőzmény-kapcsolóját és a külön energia-kvótakezelést.
A verziószám .14: a már kiadott .12 vagy .13 azonosítóját nem használjuk újra.

Nincs közös fiókfrissítési sor, nincs közös zár, nincs 20 másodperces szünet.
Egy lassú fiók nem várakoztatja a többi fiók setupját vagy adatfrissítését
az eltávolított helyi mechanizmussal. A frissítések ismét egymástól függetlenül
futhatnak, a saját mentett frissítési időközökkel.

## Ami megmarad

- **Energiaelőzmények lekérése (induláskor és újratöltéskor is)** jelölőnégyzet.
  Az alapérték kikapcsolt; egy már kifejezetten elmentett bekapcsolt értéket a
  frissítés nem ír felül. Kikapcsolva nincs induláskori, időzített vagy kézi
  újratöltés miatti `/devices/.../buckets` energiaelőzmény-lekérés.
- A tiltás a DailyDataCoordinator közvetlen frissítése előtt is érvényesül,
  tokenfrissítés és API-hozzáférés nélkül. A tiltott energiaexport és jelentés
  továbbra is jelzi, hogy előbb engedélyezni kell az opciót.
- Külön tárolt energia-kvóta és a korábban tévesen közös tárolóba mentett,
  egyértelmű `/buckets` 403-as kvóták átvezetése az eredeti határidő megőrzésével.
- Élő kazánadatok, vezérlés, gatewaydiagnosztika, magyar API-állapot,
  kézi újrapróbálkozás gomb és a korábbi fiókszintű kvótavédelem.
- Az energiaopció változása továbbra is újratölti az érintett fiókot.
- A .12-ben már meglévő, kvótalejárat utáni kis biztonsági ráhagyás és
  fiókonkénti 0–14 másodperces eltolás megmarad. Ez nem közös sorosítás,
  és nem teszi a normál frissítéseket egymástól függővé.

A `Vaillant API állapot` sorba állást jelző két .13-as attribútuma megszűnik:
`API-frissítés sorban áll`, `API-frissítés folyamatban`.
Az energiaelőzmény- és kvótaattribútumok megmaradnak. A frontend-kártyát nem kell módosítani.

## Telepítés .12 vagy .13 fölé

A csomagok kumulatívak a teljes .12 telepítéshez képest: **nem kell először
külön visszatelepíteni a .12-t**, és a már felmásolt .13-ra is rátehetők.
Régebbi vagy hiányos telepítésre ez nem teljes integrációs csomag.

1. Készíts Home Assistant-mentést, illetve másolatot az aktuális mypyllant
   mappáról a `custom_components` könyvtáron kívülre.
2. A HA ZIP-ben lévő 11 fájlt másold a meglévő
   `/config/custom_components/mypyllant/` mappába, az alkönyvtárakat megtartva.
   A `translations` fájljait is másold át. A többi régi integrációfájlt ne töröld.
3. A .13-ban hozzáadott `/config/custom_components/mypyllant/api_queue.py`
   már felesleges: törölhető. Az új kód nem importálja, ezért ottmaradva sem
   kapcsolja vissza a sorosítást. A forkban is töröld ezt az egy fájlt, amennyiben létezik.
4. **Teljes Home Assistant Core-újraindítás szükséges**, nem csak az integráció
   újratöltése. A már memóriában futó .13-as modult egy sima reload nem biztos,
   hogy lecseréli. A Proxmox hostot és a VM-et nem kell újraindítani.

```bash
ha core restart
```

A Proxmox host SFTP-útvonala nem azonos a HA konfiguráció útvonalával;
a korábban bevált VM-be másolási módszert használd.

5. A fiók Opcióiban az energiaelőzmények kapcsolója maradjon kikapcsolva.
   A szenzor attribútumai között ez ellenőrizhető:

```yaml
Energiaelőzmények lekérése: false
```

A frissítés a meglévő fiókokat, kazánokat és entitásazonosítókat nem törli.
A `.storage` fájlokat nem kell kézzel módosítani vagy törölni.
A 600/10800 másodperces vagy más korábban mentett időközöket nem írja át.

## Csomagok

- `mypyllant-component-v0.9.19.14-HA-patch.zip`: 11 új/módosított runtime fájl.
- `mypyllant-component-v0.9.19.14-modified-files.zip`: ugyanezek, verziófájlok,
  tesztek, ez az útmutató és a changelog. Csak új/módosított fájlokat tartalmaz.
- `mypyllant-v0.9.19.13-to-v0.9.19.14.patch`: a .13-hoz képesti valódi diff;
  a közös sor modulfájljának törlését is tartalmazza.

A ZIP nem töröl fájlt. A .13 `api_queue.py` eltávolítása kézi lépés vagy a diff
alkalmazásával történik. A régi `scripts/check_v013_offline.py` parancs a forkban
most a .14 tesztjeire irányít, nem próbálja a törölt sor modult importálni.

## Ellenőrzés és korlátok

36 izolált regressziós teszt sikeres:

```bash
python scripts/check_v014_offline.py
```

Valódi asyncio-végrehajtás, helyettesítő Home Assistant/API-objektumokkal.
A tesztek többek között ellenőrzik hét fiók egymástól független indulását és
frissítését, a lassú fiók melletti másik indulást, az energiaelőzmények teljes
kihagyását, a kvóták megőrzését és szétválasztását. A Python-, JSON- és
TOML-szintaktika is ellenőrizve. Az izolált teszt nem teljes Home Assistant-teszt.
A projekt teljes prek/pytest környezete nem futott le: a szükséges Python 3.14
letöltése DNS-hibával megállt. Élő Vaillant API-val nem történt teszt.

A szerveroldali 403/429 kvótát ez a visszaállítás nem szünteti meg, és a már
mentett érvényes fiókszintű várakozás megmarad. A változtatás a felhasználó által
kért helyi sorosítás eltávolítása; a .13-ban tapasztalt hiba konkrét okát napló
nélkül nem bizonyítja.
