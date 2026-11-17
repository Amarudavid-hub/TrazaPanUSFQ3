# TrazaPanUSFQ

Aplicación web local para llevar el lote de cada ingrediente desde bodega principal hasta la hoja de producción y la etiqueta del pan congelado. Corre en una PC de la panadería y se usa desde el navegador de cualquier celular, tablet o PC conectado al mismo wifi. No necesita internet para funcionar.

## Cómo fluye la información

| Paso | Quién | Qué hace en la app | Qué reemplaza |
|---|---|---|---|
| 0 | Bodega principal | **Bodega principal**: registra cada recepción con los mismos campos del registro BPM-SFFS-RRMP001, un lote por fila. El lote queda disponible al instante para bodega secundaria | El Excel de recepción |
| 1 | Encargado de producción | **Bodega secundaria**: toca los lotes que llegaron, secos y refrigerados, y escribe cuántas unidades de cada uno. Solo puede elegir lotes registrados por bodega principal y no más de lo que queda allí | Requisición sin lote |
| 2 | Chefs | **Cocina**: antes de abrir un empaque, tocan el lote y escriben cuántas unidades toman. Solo pueden elegir lo que está en bodega secundaria, secos y refrigerados | Foto por WhatsApp y hoja madre |
| 3 | Chefs | **Producción**: tocan el pan que van a empezar y, al final, «Terminar producción» | — |
| 4 | Encargado de producción | Completa cantidades, unidades y horneado en la hoja ya llena con los lotes, y la cierra | Llenado de lotes a mano |
| 5 | Encargado de producción | **Etiquetas**: descarga los códigos del día para ZebraDesigner | Escribir cada código en Zebra |

## Reglas que aplica el sistema

- **El lote se escribe una sola vez**, en bodega principal, al recibirlo. Después solo se elige de una lista. La única excepción es «No encuentro el lote en la lista», que pide el código y una foto, y deja el lote marcado *por revisar* hasta que bodega principal lo registre (entonces se confirma solo).
- **Recepciones con algo NO OK** (apariencia o transporte) piden observación y si se recibió o se rechazó. Lo rechazado queda en el registro pero no pasa a la panadería.
- **Correcciones en bodega principal** piden motivo. El código de un lote ya no se puede cambiar ni anular una vez que pasó a bodega secundaria o se usó; ahí la corrección va en Observaciones.
- **La hoja se llena con lo que está abierto en cocina** al iniciar la producción. Si durante la producción se abre otro lote de un ingrediente de la receta, se agrega solo.
- **Al abrir un lote nuevo, pregunta si el anterior se terminó.** Así los lotes agotados no siguen apareciendo en las hojas.
- **Una hoja no se puede cerrar si a un ingrediente le falta el lote.** Hay que abrirlo en Cocina, agregarlo desde la lista o marcar «No se utilizó».
- **Subrecetas** (masa madre, relleno y glacé de canela): al iniciar su producción, su código (por ejemplo `131126RE`) queda disponible como lote interno y se anota solo en el pan que lo usa. La consulta de trazabilidad baja hasta los ingredientes de la subreceta.
- **El código del pan** se arma solo: fecha `DDMMAA` + código del producto (`131126CR`). Dos producciones del mismo producto en el mismo día comparten código y la consulta muestra las dos.
- **Nada se borra.** Las correcciones (quitar o agregar un lote en una hoja, anular una producción) piden motivo y quedan en Administrar → Historial con fecha, hora y persona.
- **El código de lote se guarda tal cual está en el Excel**, sin limpiar ni normalizar.

## Instalación (Windows)

1. Instalar Python 3.10 o superior desde python.org, marcando **«Add python.exe to PATH»**.
2. Copiar la carpeta `trazapan` a la PC que quedará encendida en la panadería.
3. Doble clic en `iniciar.bat`. La primera vez instala lo necesario (requiere internet solo esa vez).
4. Si Windows pregunta por el firewall, permitir el acceso en **redes privadas**.
5. La ventana muestra dos direcciones: una para esa PC (`http://localhost:8000`) y otra para los celulares (`http://192.168.x.x:8000`). En cada celular, abrir esa dirección y usar «Agregar a pantalla de inicio».

Para apagar, cerrar la ventana. Para que arranque sola al encender la PC, poner un acceso directo de `iniciar.bat` en la carpeta de Inicio de Windows (`Win+R` → `shell:startup`).

### Lo que hay que pedir a TI (red institucional)

- **Que los dispositivos de la panadería puedan verse entre sí en el puerto 8000.** Muchas redes wifi institucionales aíslan a los clientes entre ellos; si es el caso, el celular no podrá abrir la dirección aunque esté en el mismo wifi. Hay que probarlo el primer día.
- **Una IP fija (o reserva DHCP) para la PC**, para que la dirección no cambie.
- Si TI no puede abrirlo, la alternativa es un router wifi propio dentro de la panadería, solo para esta app.

## Puesta en marcha (una sola vez)

1. **Administrar → Personas**: agregar a cada chef, al encargado y a quien recibe en bodega principal.
2. **Administrar → Importar Excel antiguo**: subir el Excel de bodega principal con lo recibido antes de usar la app (solo una vez).
3. **Administrar → Pendientes**: asignar los productos de bodega que no se reconocieron (a un ingrediente existente, a uno nuevo, o «No se usa en panadería») y revisar los lotes marcados.
4. **Administrar → Importar Excel → Limpiar lotes viejos**: marcar como agotados los lotes de meses anteriores que ya no existen.
5. **Administrar → Recetas**: verificar que cada producto tenga los ingredientes correctos y en el orden de la hoja.
6. El día de arranque: registrar en **Bodega secundaria** lo que hay físicamente ahí y abrir en **Cocina** lo que ya está abierto.

## Registro de bodega principal

- Pantalla **Bodega principal**: fecha de ingreso, producto, proveedor, lote, cantidad, unidad de medida, apariencia visual, condiciones de transporte, aprobado por y observaciones, como en el registro BPM-SFFS-RRMP001. Se agregó un campo opcional de fecha de caducidad.
- El producto y el proveedor se eligen de la lista o se escriben; al elegir un producto conocido se completan su último proveedor y su unidad.
- Un producto nuevo pide a qué ingrediente de panadería corresponde (o «No se usa en panadería», o decidir después en Pendientes). Bodega registra todo lo que recibe; solo lo de panadería pasa a las listas de lotes.
- **Un lote por fila.** Si el código parece traer varios lotes, la app avisa. «Guardar y registrar otro lote del mismo producto» deja listos producto, proveedor y unidad para el siguiente.
- **Imprimir** y **Descargar Excel** sacan el registro del mes en el formato BPM (con encabezado, código, versión y fecha de elaboración). Los datos del encabezado se editan en `FORMATO_RECEPCION`, al inicio de la sección 3b de `app.py`.

## Excel antiguo de bodega (solo una vez)

- Administrar → **Importar Excel antiguo** carga lo que se registró antes de la app. Busca la hoja con columnas `PRODUCTO` y `LOTE`, y también lee fecha, proveedor, cantidad, unidad, caducidad, apariencia, transporte, aprobado por y observaciones si existen.
- Las filas ya importadas, o ya registradas en la app, no se duplican.
- Cuando una celda trae varios lotes (`284907 - 287803 - 289490`), la app los separa y los marca *por revisar*.
- Las filas sin lote no se importan y se listan al terminar.

## Etiquetas con Zebra

En la pantalla **Etiquetas** se descarga un Excel o CSV con las columnas `LOTE, PRODUCTO, FE, FV, CANTIDAD`. Con **ZebraDesigner Professional** se conecta la plantilla de la etiqueta a ese archivo como base de datos y se imprimen todos los registros de una vez. La edición gratuita (Essentials) no permite conectar una base de datos.

## Datos y respaldos

- Todo está en un solo archivo: `datos/trazabilidad.db` (SQLite). Las fotos de lotes anotados a mano están en `datos/fotos`.
- Cada día que la app arranca guarda una copia en `datos/respaldos` (conserva 30). También se puede descargar desde Administrar → Importar Excel → Respaldo.
- Para mudar la app a otra PC, copiar la carpeta completa.

## Lo que esta versión no hace (todavía)

- **Los campos manuales de la hoja son genéricos** (cantidad por ingrediente, unidades producidas, horneado, observaciones). Hay que ajustarlos al formato real del registro BPM-SFFS-RPRO001.
- **MIX BREAD (BM) no tiene receta.** Se arma con masas de otras producciones y hay que definir cómo se registra.
- **No hay contraseñas.** Cada persona elige su nombre en su dispositivo. Cualquiera en la red puede entrar a Administrar.
- **No controla cantidades ni inventario**: sabe qué lotes están en cada lugar, no cuánto queda.
- **No lee códigos QR.** Sería el siguiente paso para que registrar sea más rápido que tomar la foto: imprimir con la Zebra una etiqueta por lote al llegar a bodega secundaria y escanearla al abrir.
- Las recetas y los nombres de ingredientes iniciales se tomaron de las hojas de producción de febrero a octubre de 2026; deben validarse con el recetario.

## Actualizar a una versión nueva

Reemplazar `app.py`, `semilla.json`, `requirements.txt`, `LEEME.md` y la carpeta `static`. **No tocar la carpeta `datos`**: ahí está todo lo registrado. La base se actualiza sola al arrancar.

## Para quien herede el código

| Archivo | Contenido |
|---|---|
| `app.py` | Servidor (Flask) y toda la lógica. Está dividido en 8 secciones comentadas |
| `static/app.js` | Pantallas (JavaScript sin librerías) |
| `static/estilos.css` | Estilos |
| `semilla.json` | Ingredientes, productos y recetas iniciales; solo se carga cuando la base está vacía |
| `requirements.txt` | Dependencias: flask, openpyxl, waitress |

Tablas principales: `recepciones` (registro de bodega principal, hecho en la app o importado), `lotes` (un código de lote por ingrediente), `aperturas` (cuándo estuvo abierto cada lote en cocina), `producciones`, `produccion_lineas` y `produccion_lotes` (la hoja), `movimientos` (bitácora). La definición completa está al inicio de `app.py`.
