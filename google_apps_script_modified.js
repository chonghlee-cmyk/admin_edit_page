// ── 설정값 ──────────────────────────────────────────
const SOURCE_SHEET_ID = '1V-lMYW4nZIKtUdqJYhNEaNRYv29IPCUpxfYZPaU6yfk';
const SOURCE_SHEET_NAME = '작품';
const TARGET_SHEET_ID = '15oKfEX-O4PBdhw5Cd9KXzSiuArPLvtTwk8ROxdwClzQ';
const TARGET_SHEET_NAME = '작품관리대장';
const WORK_SHEET_ID = '15oKfEX-O4PBdhw5Cd9KXzSiuArPLvtTwk8ROxdwClzQ';
const LOG_SHEET_NAME = '동기화 로그';
const LANGUAGE_TABS = ['EN', 'FR', 'ES', 'PT', 'IT', 'DE', 'ZH', 'JP', 'TH'];
// ────────────────────────────────────────────────────

function col(letter) {
  if (!letter) return -1;
  letter = letter.toUpperCase();
  let n = 0;
  for (let i = 0; i < letter.length; i++) {
    n = n * 26 + (letter.charCodeAt(i) - 64);
  }
  return n - 1;
}

// ── 로그 기록 ─────────────────────────────────────────
function writeLog(type, status) {
  const ss = SpreadsheetApp.openById(WORK_SHEET_ID);
  let logSheet = ss.getSheetByName(LOG_SHEET_NAME);

  if (!logSheet) {
    logSheet = ss.insertSheet(LOG_SHEET_NAME);
    logSheet.getRange('A1:C1').setValues([['시간', '종류', '상태']]);
    logSheet.getRange('A1:C1').setFontWeight('bold');
    logSheet.setColumnWidth(1, 200);
    logSheet.setColumnWidth(2, 150);
    logSheet.setColumnWidth(3, 100);
  }

  const now = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss');
  logSheet.appendRow([now, type, status]);
}

// ── BT~CC에서 수식이 있는 마지막 행 찾기 ──
function findLastFormulaRow(sheet, startCol) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return 0;

  const formulas = sheet.getRange(2, startCol, lastRow - 1, 1).getFormulas();

  for (let i = formulas.length - 1; i >= 0; i--) {
    if (formulas[i][0]) {
      return i + 2;
    }
  }
  return 0;
}

// ── 수식을 새로운 행까지 확장 (R1C1 표기로 상대 참조 보존) ──
function copyFormulasDown(sheet, sourceRow, targetLastRow, startCol, endCol) {
  if (sourceRow >= targetLastRow) return;

  const numCols = endCol - startCol + 1;
  const numNewRows = targetLastRow - sourceRow;

  const sourceFormulas = sheet.getRange(sourceRow, startCol, 1, numCols).getFormulasR1C1()[0];
  const newFormulas = Array.from({length: numNewRows}, () => [...sourceFormulas]);

  sheet.getRange(sourceRow + 1, startCol, numNewRows, numCols).setFormulasR1C1(newFormulas);
}

// ── 각 언어 탭에 작품번호, 플랫폼, KR상태 복사 ──
//   · 기존 행: B/C(플랫폼/KR상태)를 매번 최신값으로 갱신 (D~ 크롤 데이터는 보존)
//   · 신규 행: 맨 아래에 추가
function copyToLanguageSheets(tgtSheet, dataRows) {
  const ADMIN_START = 2;

  const ss = SpreadsheetApp.openById(TARGET_SHEET_ID);

  const colA = col('A');
  const colP = col('P');
  const colBT = col('BT');

  // 1️⃣ 작품관리대장에서 A, P, BT 한 번에 읽어 작품번호 → [플랫폼, KR상태] 맵 구성
  const sourceData = tgtSheet
    .getRange(ADMIN_START, 1, dataRows, colBT + 1)
    .getValues();

  const infoMap = new Map();   // keyA → [플랫폼, KR상태]
  const orderedKeys = [];      // 신규 추가 시 작품관리대장 순서 유지
  for (const row of sourceData) {
    const keyA = String(row[colA]).trim();
    if (keyA === '') continue;
    if (!infoMap.has(keyA)) {
      infoMap.set(keyA, [row[colBT] ?? '', row[colP] ?? '']);
      orderedKeys.push(keyA);
    }
  }

  // 2️⃣ 각 언어 탭: 기존 행 B/C 갱신 + 신규 행 append
  for (const lang of LANGUAGE_TABS) {
    const langSheet = ss.getSheetByName(lang);
    if (!langSheet) continue;

    const existingKeys = new Set();
    const langLastRow = langSheet.getLastRow();

    // 2-1) 기존 행 A/B/C 읽어서 B/C를 최신값으로 갱신 (A·D~ 는 그대로)
    if (langLastRow >= ADMIN_START) {
      const abcRange = langSheet.getRange(ADMIN_START, 1, langLastRow - ADMIN_START + 1, 3);
      const abc = abcRange.getValues();

      for (let i = 0; i < abc.length; i++) {
        const k = String(abc[i][0]).trim();
        if (k === '') continue;
        existingKeys.add(k);
        if (infoMap.has(k)) {
          const [plat, stat] = infoMap.get(k);
          abc[i][1] = plat;   // B: 플랫폼
          abc[i][2] = stat;   // C: KR상태
        }
      }
      abcRange.setValues(abc);
    }

    // 2-2) 언어 탭에 없는 신규 작품만 맨 아래 추가
    const newRows = [];
    for (const k of orderedKeys) {
      if (existingKeys.has(k)) continue;
      const [plat, stat] = infoMap.get(k);
      newRows.push([k, plat, stat]);
    }

    if (newRows.length > 0) {
      const appendStartRow = Math.max(langSheet.getLastRow() + 1, ADMIN_START);
      langSheet
        .getRange(appendStartRow, 1, newRows.length, 3)
        .setValues(newRows);
      Logger.log(`${lang} 탭: 기존 갱신 + 신규 ${newRows.length}행 추가`);
    } else {
      Logger.log(`${lang} 탭: 기존 갱신 완료 (신규 없음)`);
    }
  }

  Logger.log('모든 언어 탭에 작품정보 갱신 완료');
  writeLog('언어별 탭 갱신', '✅ 완료');
}

// ── 정렬 복사하여 동기화 ──────────────────────────────
function syncWithMapping() {
  const MAPPING = [
    [col('C'), col('A')],
    [col('D'), col('B')],
    [col('E'), col('C')],
    [col('H'), col('D')],
    [col('I'), col('E')],
    [col('B'), col('F')],  // 원본 B → 타겟 F
    [col('G'), col('G')],
    [col('A'), col('H')],
    [col('F'), col('I')],
    ...Array.from({length: 17}, (_, i) => [col('Q') + i, col('J') + i]),
    ...Array.from({length: 5},  (_, i) => [col('AH') + i, col('AA') + i]),
    ...Array.from({length: 5},  (_, i) => [col('AW') + i, col('AF') + i]),
    ...Array.from({length: 5},  (_, i) => [col('BB') + i, col('AK') + i]),
    ...Array.from({length: 5},  (_, i) => [col('BG') + i, col('AP') + i]),
    ...Array.from({length: 5},  (_, i) => [col('BL') + i, col('AU') + i]),
    ...Array.from({length: 5},  (_, i) => [col('BQ') + i, col('AZ') + i]),
    ...Array.from({length: 5},  (_, i) => [col('AR') + i, col('BE') + i]),
    ...Array.from({length: 5},  (_, i) => [col('BV') + i, col('BJ') + i]),
    ...Array.from({length: 5},  (_, i) => [col('CA') + i, col('BO') + i]),
  ];

  const srcSheet = SpreadsheetApp
    .openById(SOURCE_SHEET_ID)
    .getSheetByName(SOURCE_SHEET_NAME);

  const tgtSheet = SpreadsheetApp
    .openById(TARGET_SHEET_ID)
    .getSheetByName(TARGET_SHEET_NAME);

  const lastRow = srcSheet.getLastRow();
  const lastCol = srcSheet.getLastColumn();

  if (lastRow === 0 || lastCol === 0) {
    Logger.log('소스 시트가 비어 있습니다.');
    writeLog('정렬 복사', '❌ 소스 비어있음');
    return;
  }

  const SRC_START = 4;
  const TGT_START = 2;
  const TGT_WIDTH = col('BS') + 1;
  const FORMULA_START_COL = col('BT') + 1;
  const FORMULA_END_COL = col('CC') + 1;

  const dataRows = lastRow - SRC_START + 1;

  // 1️⃣ 소스에서 값 + 메모를 각각 한 번에 읽기
  const srcRange = srcSheet.getRange(SRC_START, 1, dataRows, lastCol);
  const srcValues = srcRange.getValues();
  const srcNotes  = srcRange.getNotes();

  // 2️⃣ 메모리 상에서 타겟 형태로 재배열 (값 + 메모)
  const tgtValues = Array.from({length: dataRows}, () => new Array(TGT_WIDTH).fill(''));
  const tgtNotes  = Array.from({length: dataRows}, () => new Array(TGT_WIDTH).fill(''));
  for (const [src, tgt] of MAPPING) {
    for (let r = 0; r < dataRows; r++) {
      tgtValues[r][tgt] = srcValues[r][src] ?? '';
      tgtNotes[r][tgt]  = srcNotes[r][src]  ?? '';
    }
  }

  // 3️⃣ 타겟의 기존 데이터 영역 크기 파악
  const tgtLastRow = tgtSheet.getLastRow();
  const newLastRow = TGT_START + dataRows - 1;

  // 4️⃣ 한 번에 일괄 쓰기 (값 + 메모)
  const tgtRange = tgtSheet.getRange(TGT_START, 1, dataRows, TGT_WIDTH);
  tgtRange.setValues(tgtValues);
  tgtRange.setNotes(tgtNotes);

  // 5️⃣ 기존 데이터가 새 데이터보다 길었다면 잔여 행 값/메모 모두 삭제
  if (tgtLastRow > newLastRow) {
    const leftover = tgtSheet.getRange(newLastRow + 1, 1, tgtLastRow - newLastRow, TGT_WIDTH);
    leftover.clearContent();
    leftover.clearNote();
  }

  // 6️⃣ BT~CC 수식 자동 확장
  const lastFormulaRow = findLastFormulaRow(tgtSheet, FORMULA_START_COL);
  if (lastFormulaRow > 0 && lastFormulaRow < newLastRow) {
    copyFormulasDown(tgtSheet, lastFormulaRow, newLastRow, FORMULA_START_COL, FORMULA_END_COL);
  }

  // 7️⃣ BT 수식 계산 완료 대기 후 각 언어 탭에 신규 작품정보 추가
  SpreadsheetApp.flush();
  copyToLanguageSheets(tgtSheet, dataRows);

  Logger.log('정렬 복사 완료: ' + new Date() + ' (총 ' + dataRows + '행)');
  writeLog('정렬 복사', '✅ 완료 (언어별 탭 업데이트)');
}

// ── 전체 동기화 ──────────────────────────────────────
function syncAll() {
  syncWithMapping();
}

// ── 메뉴 ─────────────────────────────────────────────
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('🔄 동기화')
    .addItem('동기화 실행', 'syncAll')
    .addToUi();
}
