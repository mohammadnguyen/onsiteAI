import * as Print from 'expo-print';
import type { TFunction } from 'i18next';

import { api } from '../api/client';
import { ExportError, type ExportParams } from '../api/reports';
import { buildExpenseReportHtml, type ReportData } from './expenseReportHtml';

/**
 * Fetch the report data and render it to a PDF on the device.
 *
 * Why on-device rather than server-side: the staging backend runs on a
 * 512MB shared VM, and a headless browser for print would not fit it.
 * iOS renders with WebKit, which handles the report's CSS and embeds
 * CJK glyphs from the system font — so the device is both the cheaper
 * and the higher-fidelity option here.
 *
 * Errors reuse {@link ExportError} so the export screen keeps ONE error
 * vocabulary for both the Excel and the PDF path.
 */
export async function exportExpenseReportPdf(
  params: ExportParams,
  t: TFunction,
  locale: string,
): Promise<{ uri: string; mimeType: string; uti: string }> {
  let data: ReportData;
  try {
    const r = await api.get<ReportData>('/reports/expenses-report', {
      params: {
        from_date: params.fromDate,
        to_date: params.toDate,
        // Sent only when ON so OFF exercises the backend's default.
        ...(params.includePending ? { include_pending: true } : {}),
      },
    });
    data = r.data;
  } catch (err) {
    const status = (err as { response?: { status?: number } })?.response
      ?.status;
    if (status === 401) throw new ExportError('session_expired', status);
    if (status === 403) throw new ExportError('forbidden', status);
    if (status === 400) throw new ExportError('bad_dates', status);
    if (status == null) throw new ExportError('network');
    throw new ExportError('generic', status);
  }

  const html = buildExpenseReportHtml(data, t, locale);
  try {
    const { uri } = await Print.printToFileAsync({ html, base64: false });
    return { uri, mimeType: 'application/pdf', uti: 'com.adobe.pdf' };
  } catch {
    // Print failures are local (no disk, renderer unavailable) — not
    // an auth or network condition, so they stay generic.
    throw new ExportError('generic');
  }
}
