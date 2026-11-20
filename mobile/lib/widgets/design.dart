import 'package:flutter/material.dart';

import '../main.dart';

/*
  Shared building blocks of the app's design system, matching the web app
  (frontend/src/App.css: .kpi-card, .org-mark, .brand-logo). Use these instead of
  styling each screen on its own, so web and mobile look like one product.

    KpiCard / KpiGrid   number cards: icon tile, accent line, tone colour
    BrandLogo           the product logo (assets/logo.png, same as the web favicon)
    OrgMark             the organisation's initial on a white tile (Super Admin portal)
    SectionTitle        a section heading with an optional action on the right
*/

/// Accent colours of KPI cards (web: .kpi-card.tone-*).
enum KpiTone { brand, teal, pos, act, neutral }

(Color, Color) kpiColors(KpiTone tone) => switch (tone) {
      KpiTone.brand => (AppColors.brand, AppColors.brandSoft),
      KpiTone.teal => (
          AppColors.isDark ? AppColors.tealLight : const Color(0xFF2F7489),
          AppColors.isDark ? const Color(0xFF12303A) : const Color(0xFFE1F0F4),
        ),
      KpiTone.pos => (AppColors.pos, AppColors.posSoft),
      KpiTone.act => (AppColors.warn, AppColors.warnSoft),
      KpiTone.neutral => (AppColors.steel, AppColors.neutralSoft),
    };

/// A number card: label, big value, a short note, an icon tile in the card's colour and
/// a thin accent line on top. [valueWidget] replaces the value (e.g. a rating with stars).
class KpiCard extends StatelessWidget {
  const KpiCard({
    super.key,
    required this.label,
    required this.icon,
    this.value = '',
    this.valueWidget,
    this.note,
    this.tone = KpiTone.brand,
    this.live = false,
    this.onTap,
  });

  final String label;
  final IconData icon;
  final String value;
  final Widget? valueWidget;
  final String? note;
  final KpiTone tone;

  /// Shows a green "live" dot after the label (something happening now).
  final bool live;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final (color, soft) = kpiColors(tone);
    final card = Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppColors.line),
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [AppColors.surface, AppColors.surface, Color.alphaBlend(soft.withValues(alpha: 0.7), AppColors.surface)],
          stops: const [0, 0.55, 1],
        ),
        boxShadow: [
          if (!AppColors.isDark) BoxShadow(color: const Color(0xFF001D39).withValues(alpha: 0.05), blurRadius: 12, offset: const Offset(0, 4)),
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(13),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(height: 3, color: color),
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 11, 12, 13),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(children: [
                          Flexible(
                            child: Text(label,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: AppColors.ink2, height: 1.25)),
                          ),
                          if (live) ...[
                            const SizedBox(width: 6),
                            Container(
                              width: 8,
                              height: 8,
                              decoration: BoxDecoration(
                                color: AppColors.pos,
                                shape: BoxShape.circle,
                                boxShadow: [BoxShadow(color: AppColors.pos.withValues(alpha: 0.3), spreadRadius: 3)],
                              ),
                            ),
                          ],
                        ]),
                        const SizedBox(height: 6),
                        valueWidget ??
                            Text(value,
                                style: TextStyle(
                                  fontFamily: kDisplayFont,
                                  fontSize: 26,
                                  fontWeight: FontWeight.w800,
                                  height: 1.05,
                                  color: AppColors.ink,
                                )),
                        if (note != null && note!.isNotEmpty) ...[
                          const SizedBox(height: 4),
                          Text(note!, style: TextStyle(fontSize: 12.5, color: AppColors.ink2, height: 1.3)),
                        ],
                      ],
                    ),
                  ),
                  const SizedBox(width: 8),
                  Container(
                    width: 34,
                    height: 34,
                    decoration: BoxDecoration(
                      color: soft,
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: color.withValues(alpha: 0.14)),
                    ),
                    child: Icon(icon, size: 18, color: color),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
    if (onTap == null) return card;
    return Material(
      color: Colors.transparent,
      child: InkWell(borderRadius: BorderRadius.circular(14), onTap: onTap, child: card),
    );
  }
}

/// KPI cards two per row (the web's phone layout); an odd last card, or one marked
/// [wide], spans the full row.
class KpiGrid extends StatelessWidget {
  const KpiGrid({super.key, required this.cards, this.wide = const {}, this.gap = 10});

  final List<Widget> cards;

  /// Indexes of cards that take the whole row (e.g. a sentiment bar).
  final Set<int> wide;
  final double gap;

  @override
  Widget build(BuildContext context) {
    final rows = <Widget>[];
    var i = 0;
    while (i < cards.length) {
      final fullRow = wide.contains(i) || i == cards.length - 1 || wide.contains(i + 1);
      if (fullRow) {
        rows.add(cards[i]);
        i += 1;
      } else {
        rows.add(IntrinsicHeight(
          child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Expanded(child: cards[i]),
            SizedBox(width: gap),
            Expanded(child: cards[i + 1]),
          ]),
        ));
        i += 2;
      }
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (var k = 0; k < rows.length; k++) ...[
          if (k > 0) SizedBox(height: gap),
          rows[k],
        ],
      ],
    );
  }
}

/// The product logo (web: public/favicon.svg).
class BrandLogo extends StatelessWidget {
  const BrandLogo({super.key, this.size = 28});

  final double size;

  @override
  Widget build(BuildContext context) => Image.asset(
        'assets/logo.png',
        width: size,
        height: size,
        filterQuality: FilterQuality.medium,
        semanticLabel: 'FMCG Video Learning Engine',
      );
}

/// The organisation's initial on a tile (web: .org-mark). Navy tile on light bars,
/// white tile when [onDark] (navy headers).
class OrgMark extends StatelessWidget {
  const OrgMark({super.key, required this.name, this.size = 30, this.onDark = false});

  final String name;
  final double size;
  final bool onDark;

  @override
  Widget build(BuildContext context) {
    final initial = name.trim().isEmpty ? '?' : name.trim()[0].toUpperCase();
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: onDark || AppColors.isDark ? Colors.white : AppColors.navy,
        borderRadius: BorderRadius.circular(size * 0.27),
      ),
      child: Text(initial,
          style: TextStyle(
            fontFamily: kDisplayFont,
            fontSize: size * 0.5,
            fontWeight: FontWeight.w800,
            color: onDark || AppColors.isDark ? AppColors.navy : Colors.white,
          )),
    );
  }
}

/// A section heading ("Products", "Recent feedback") with an optional action.
class SectionTitle extends StatelessWidget {
  const SectionTitle(this.text, {super.key, this.action, this.subtitle});

  final String text;
  final String? subtitle;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(text, style: TextStyle(fontFamily: kDisplayFont, fontSize: 18, fontWeight: FontWeight.w800, color: AppColors.ink)),
              if (subtitle != null) ...[
                const SizedBox(height: 2),
                Text(subtitle!, style: TextStyle(fontSize: 12.5, color: AppColors.muted)),
              ],
            ]),
          ),
          ?action,
        ],
      ),
    );
  }
}
