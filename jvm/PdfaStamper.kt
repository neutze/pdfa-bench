// Lanes K1 and K1i: the text-layer stamp a Kotlin microservice would run, on PDFBox 3.
//
// Same recipe as the Python graft lanes, so the library is the variable:
//   - one appended content stream per page with words, text render mode 3 (invisible),
//     placed in user space -- never through an "overlay page" helper, which counter-rotates on /Rotate pages
//   - one embedded, subset Type0 font
//   - font size fitted to the word box width, clamped to 0.4..1.6 x the box height
//   - output intent as decided by the caller from the file's device colours: rgb, cmyk, or none
//     (none: the file cannot be PDF/A without touching content it doesn't own, so it is not marked)
//   - XMP pdfaid part 2 conformance B, carrying the same values as the Info dictionary
// Input words are TSV: page index, x0, y0, x1, y1 (normalised, top-left, page as displayed), text.
//
// usage: full|incremental <in.pdf> <words.tsv> <out.pdf> <font.ttf> rgb|cmyk|none <srgb.icc> <cmyk.icc>

import org.apache.pdfbox.Loader
import org.apache.pdfbox.cos.COSName
import org.apache.pdfbox.pdmodel.PDDocument
import org.apache.pdfbox.pdmodel.PDPage
import org.apache.pdfbox.pdmodel.PDPageContentStream
import org.apache.pdfbox.pdmodel.common.PDMetadata
import org.apache.pdfbox.pdmodel.font.PDType0Font
import org.apache.pdfbox.pdmodel.graphics.color.PDOutputIntent
import org.apache.pdfbox.pdmodel.graphics.state.RenderingMode
import org.apache.pdfbox.util.Matrix
import org.apache.xmpbox.XMPMetadata
import org.apache.xmpbox.xml.XmpSerializer
import java.io.ByteArrayOutputStream
import java.io.File

private const val BASELINE = 0.22

data class Word(val page: Int, val x0: Double, val y0: Double, val x1: Double, val y1: Double, val text: String)

fun readWords(file: File): List<Word> = file.readLines().filter { it.isNotBlank() }.map { line ->
    val p = line.split('\t', limit = 6)
    Word(p[0].toInt(), p[1].toDouble(), p[2].toDouble(), p[3].toDouble(), p[4].toDouble(), p[5])
}

/** Normalised top-left displayed point -> PDF user space. Mirrors common.displayed_to_user. */
fun displayedToUser(page: PDPage, u: Double, v: Double): Pair<Double, Double> {
    val crop = page.cropBox
    val cx0 = crop.lowerLeftX.toDouble(); val cy0 = crop.lowerLeftY.toDouble()
    val cx1 = crop.upperRightX.toDouble(); val cy1 = crop.upperRightY.toDouble()
    val cw = cx1 - cx0; val ch = cy1 - cy0
    return when (((page.rotation % 360) + 360) % 360) {
        90 -> Pair(cx0 + v * cw, cy0 + u * ch)
        180 -> Pair(cx1 - u * cw, cy0 + v * ch)
        270 -> Pair(cx1 - v * cw, cy1 - u * ch)
        else -> Pair(cx0 + u * cw, cy1 - v * ch)
    }
}

fun stamp(doc: PDDocument, words: List<Word>, fontFile: File): Pair<Int, Int> {
    if (words.isEmpty()) return Pair(0, 0) // a file that already has text: PDF/A only, no font added
    val font = PDType0Font.load(doc, fontFile)
    var stamped = 0
    var skipped = 0
    for ((index, pageWords) in words.groupBy { it.page }) {
        if (index >= doc.numberOfPages) continue
        val page = doc.getPage(index)
        val rotate = ((page.rotation % 360) + 360) % 360
        val crop = page.cropBox
        val (dw, dh) = if (rotate == 90 || rotate == 270) Pair(crop.height, crop.width) else Pair(crop.width, crop.height)
        PDPageContentStream(doc, page, PDPageContentStream.AppendMode.APPEND, true, true).use { stream ->
            stream.beginText()
            stream.setRenderingMode(RenderingMode.NEITHER)
            for (word in pageWords) {
                val widthPt = (word.x1 - word.x0) * dw
                val heightPt = (word.y1 - word.y0) * dh
                val (x, y) = displayedToUser(page, word.x0, word.y1 - BASELINE * (word.y1 - word.y0))
                try {
                    val unitWidth = font.getStringWidth(word.text) / 1000.0
                    val fitted = if (unitWidth > 0) widthPt / unitWidth else heightPt
                    val size = fitted.coerceIn(maxOf(heightPt * 0.4, 1.0), maxOf(heightPt * 1.6, 1.0)).toFloat()
                    stream.setFont(font, size)
                    stream.setTextMatrix(Matrix.getRotateInstance(Math.toRadians(rotate.toDouble()), x.toFloat(), y.toFloat()))
                    stream.showText(word.text)
                    stamped++
                } catch (e: IllegalArgumentException) { // glyph missing from the font
                    skipped++
                }
            }
            stream.endText()
        }
        page.cosObject.isNeedToBeUpdated = true
    }
    return Pair(stamped, skipped)
}

fun markPdfA(doc: PDDocument, profile: File, channels: Int, identifier: String) {
    val catalog = doc.documentCatalog
    if (catalog.outputIntents.isEmpty()) {
        val intent = profile.inputStream().use { PDOutputIntent(doc, it) }
        intent.destOutputIntent.setInt(COSName.N, channels)
        intent.info = profile.nameWithoutExtension
        intent.outputCondition = profile.nameWithoutExtension
        intent.outputConditionIdentifier = identifier
        if (channels == 3) intent.registryName = "http://www.color.org"
        catalog.addOutputIntent(intent)
    }
    // XMP must agree with the Info dictionary (PDF/A-2 6.6.3), so every Info value is carried over.
    val info = doc.documentInformation
    val xmp = XMPMetadata.createXMPMetadata()
    val id = xmp.createAndAddPDFAIdentificationSchema()
    id.part = 2
    id.conformance = "B"
    if (info.title != null || info.author != null || info.subject != null) {
        val dc = xmp.createAndAddDublinCoreSchema()
        info.title?.let { dc.title = it }
        info.author?.let { dc.addCreator(it) }
        info.subject?.let { dc.addDescription(null, it) }
    }
    if (info.producer != null || info.keywords != null) {
        val pdf = xmp.createAndAddAdobePDFSchema()
        info.producer?.let { pdf.producer = it }
        info.keywords?.let { pdf.keywords = it }
    }
    if (info.creator != null || info.creationDate != null || info.modificationDate != null) {
        val basic = xmp.createAndAddXMPBasicSchema()
        info.creator?.let { basic.creatorTool = it }
        info.creationDate?.let { basic.createDate = it }
        info.modificationDate?.let { basic.modifyDate = it }
    }
    val bytes = ByteArrayOutputStream().also { XmpSerializer().serialize(xmp, it, true) }.toByteArray()
    val metadata = PDMetadata(doc)
    metadata.importXMPMetadata(bytes)
    catalog.metadata = metadata
    catalog.cosObject.isNeedToBeUpdated = true
}

fun main(args: Array<String>) {
    require(args.size == 8) { "usage: full|incremental <in.pdf> <words.tsv> <out.pdf> <font.ttf> rgb|cmyk|none <srgb.icc> <cmyk.icc>" }
    val mode = args[0]
    val output = File(args[3])
    Loader.loadPDF(File(args[1])).use { doc ->
        val (stamped, skipped) = stamp(doc, readWords(File(args[2])), File(args[4]))
        when (args[5]) {
            "rgb" -> markPdfA(doc, File(args[6]), 3, "sRGB IEC61966-2.1")
            "cmyk" -> markPdfA(doc, File(args[7]), 4, "Custom")
            "none" -> Unit
            else -> error("unknown intent ${args[5]}")
        }
        when (mode) {
            "full" -> doc.save(output)
            "incremental" -> output.outputStream().use { doc.saveIncremental(it) }
            else -> error("unknown mode $mode")
        }
        println("stamped=$stamped skipped=$skipped intent=${args[5]}")
    }
}
