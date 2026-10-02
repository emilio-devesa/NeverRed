---
layout: page
title: Guía de uso
permalink: /guia/
---

# Guía de uso

## Instalar en macOS (primera vez)

1. Descarga el `.dmg` de [Releases](https://github.com/emilio-devesa/NeverRed/releases) (vale para Intel y Apple Silicon), ábrelo y arrastra **NeverRed** a **Aplicaciones**.
2. Haz doble clic en NeverRed. Como la app aún no lleva firma de pago de Apple, macOS la bloquea con el aviso **"No se ha abierto NeverRed.app"**. No es un virus: pulsa **Aceptar**.

   ![Aviso de Gatekeeper](NeverRed-MacOS-GateKeeper-Alerta.png)
3. Abre **Ajustes → Privacidad y seguridad**, baja hasta el apartado de seguridad y verás el aviso del bloqueo con el botón **Abrir igualmente**. Púlsalo y confirma una vez más.

   ![Abrir igualmente en Privacidad y seguridad](NeverRed-MacOS-Ajustes-Privacidad-y-seguridad.png)

   ![Confirmación](NeverRed-MacOS-GateKeeper-Confirmación.png)
4. La app arranca su servidor y se abre en tu navegador. Solo pasa la primera vez.

A partir de ahí la app abre con normalidad. Las siguientes actualizaciones llegan solas desde la propia app y no necesitan repetir este paso.

> **Atajo por Terminal**: si prefieres saltarte los pasos 2 y 3, ejecuta una sola vez `xattr -cr /Applications/NeverRed.app` antes del primer doble clic (elimina la marca de descarga de internet).

## Entrar

Lo primero es el inicio de sesión, con casilla **Recuérdame**. Encima verás
quién ha usado la app en ese navegador, con avatar, nombre y correo
enmascarado (`dem***@...`): un clic y solo pide la contraseña.
¿Nuevo? Pestaña **Registrarse**. ¿Curioso? **Demo**, siempre la última de la
lista: entra directo con 6 meses de datos de prueba (se restablecen solos,
sin pedir contraseña y sin tocar a otros usuarios).

## La idea en 30 segundos

Cada movimiento se registra **dos veces** por el mismo importe: un **debe** y un **haber**. Ejemplo: cobras 1.500 € → `Debe: Banco 1.500` / `Haber: Sueldos 1.500`. Si algo no cuadra, NeverRed no te deja guardarlo.

## Inicio

Vista general: ecuación fundamental en vivo, patrimonio neto, gráfico de ingresos vs gastos (6 meses), últimos asientos y accesos rápidos (sueldo, gasto, transferencia). Si llevas más de 30 días sin exportar copia, verás un recordatorio con botón directo (Exportar cuenta como hecha).

## Diario

El libro de todos tus asientos: crear (con N líneas), editar, **duplicar**, marcar como **↻ mensual**, borrar y buscar. Se ven mes a mes con **‹ Mes anterior / Mes siguiente ›** encima de la lista (50 por tanda, con botón **Mostrar más** si hay más). Si eliges un rango de **fechas Desde/Hasta** (aunque cruce meses), la navegación por mes se oculta y el título muestra el rango; **Volver al mes en curso** desactiva el filtro. Botón **Importar CSV** para traer movimientos del banco (`fecha;descripción;importe`).

Debajo vive **Asientos recurrentes**: plantillas mensuales (nómina, alquiler) y el botón **Generar pendientes del mes**, que crea lo que falte sin duplicar.

## Mayor

Movimientos y saldo acumulado de cada cuenta, con su naturaleza (**deudora**: Activo/Gasto; **acreedora**: Pasivo/Patrimonio/Ingreso) y filtro por texto. El enlace **Vista mensual** muestra un mes con su navegación (‹ Mes anterior / Mes siguiente ›) y fila de **saldo inicial**; **Vista global** devuelve la lista completa.

## Cuentas

Tu plan de cuentas en 5 familias. Crea, edita o archiva cuentas: archivar oculta la cuenta de los desplegables, pero su saldo sigue contando en todos los informes.

## Informes

Balance general y PyG lado a lado arriba; debajo, **Evolución de los activos** y **Evolución de los pasivos** (saldo diario en ~180 días más su total, con valor al pasar el puntero y tabla de fines de mes) y el **Balance de comprobación** (descargable en CSV): las tres tarjetas van plegables para una página más recogida. Cierran la página los **presupuestos del mes**: pon un límite a cada gasto y te avisamos al 80 % y al superarlos. El enlace **Vista mensual** del resultado muestra un mes con su navegación (‹ Mes anterior / Mes siguiente ›); **Volver a la vista acumulada** devuelve el total.

## Herramientas

Tres utilidades que no tocan tu contabilidad.

**Mercado**: añade tickers de valores cotizados (AAPL, GOOG, AIR.MC…) y ve su gráfica de los últimos meses en verde (al alza), rojo (a la baja) o blanco (plana), con máximos, mínimos y variación del periodo; el nombre de cada ticker enlaza a su ficha de Yahoo Finanzas. Necesita tu **clave gratuita de Alpha Vantage** (25 consultas/día): la pides en un minuto en [alphavantage.co/support](https://www.alphavantage.co/support/) y la pegas al final de la pestaña. Cada usuario guarda la suya propia; la app te muestra cuánta cuota diaria te queda y guarda cada serie en caché 24 h para no gastarla. Si algo falla, el mensaje te dice si es la conexión, el ritmo de peticiones o la cuota diaria.

**Simulador de préstamos**: importe, TIN anual y años → cuota mensual francesa, total e intereses, con gráfica anual apilada (capital/intereses) y tabla año por año.

**Simulador de rendimientos**: capital inicial, aportación mensual, rentabilidad anual y años → aportado, valor final y ganancia con interés compuesto mensual, con gráfica anual apilada y tabla año por año.

Todo se calcula en tu navegador: nada se guarda ni se envía.

## Tu cuenta

En el pie: cambiar contraseña, ver sesiones activas (y cerrar las demás) o eliminar tu cuenta con todos sus datos. ¿Olvidaste la clave? Usa el enlace de recuperación del login.

## Sincronización entre dispositivos

La insignia junto a tu correo indica el estado: ✓ sincronizado, ● subiendo, ⚠ revisa. Si editas en dos sitios a la vez, la app te avisa y eliges: recargar esos datos o mantener los tuyos.

## Cerrar la app (macOS)

NeverRed vive en el Dock mientras está abierta. Al salir de ella (cmd+Q o clic derecho → Salir) se detiene el servidor sin dejar actividad en segundo plano. Además, si cierras su **última pestaña** en el navegador, todo se apaga solo en unos 20 segundos. Tus otras pestañas no se ven afectadas.

## Actualizaciones (macOS)

Al abrir la app se comprueba si hay versión nueva: verás el diálogo del sistema con los cambios y tres opciones — **Instalar** (descarga, sustituye y reabre sola), **Omitir versión** o **Más tarde**.
