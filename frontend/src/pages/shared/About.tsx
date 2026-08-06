const cardTitleStyle: React.CSSProperties = { marginBottom: '12px' };
const bodyStyle: React.CSSProperties = { color: 'var(--text-secondary)', lineHeight: 1.6, fontSize: '0.9rem' };
const sectionGap: React.CSSProperties = { marginBottom: '20px' };

function Callout({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: '0.85rem',
        color: 'var(--text-secondary)',
        padding: '10px 12px',
        borderRadius: '6px',
        background: 'rgba(217,119,6,0.08)',
        borderLeft: '3px solid var(--color-amber)',
        margin: '12px 0',
      }}
    >
      {children}
    </div>
  );
}

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: '12px', marginBottom: '14px' }}>
      <div
        style={{
          flexShrink: 0,
          width: '26px',
          height: '26px',
          borderRadius: '50%',
          background: 'var(--color-blue)',
          color: '#fff',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '0.8rem',
          fontWeight: 700,
        }}
      >
        {n}
      </div>
      <div style={{ ...bodyStyle, paddingTop: '3px' }}>{children}</div>
    </div>
  );
}

export default function About() {
  return (
    <div style={{ maxWidth: '860px' }}>
      <div className="glass-card" style={sectionGap}>
        <h3 className="card-title" style={cardTitleStyle}>Що це за програма</h3>
        <p style={bodyStyle}>
          SmartBESS EMS — платформа для українського BESS-накопичувача (батарейної системи зберігання енергії),
          що заробляє на арбітражі цін ринку «на добу наперед» (РДН). Повний ланцюжок роботи: прогноз ціни (ML) →
          MILP-оптимізація графіка заряд/розряд → диспетчеризація → SCADA-симуляція → фінансова звітність для
          інвестора.
        </p>
      </div>

      <div className="glass-card" style={sectionGap}>
        <h3 className="card-title" style={cardTitleStyle}>На основі яких даних рахується прогноз</h3>
        <p style={bodyStyle}>
          Головний принцип проєкту — жодних вигаданих даних. Прогноз рахується виключно на реальних джерелах:
        </p>
        <ul style={{ ...bodyStyle, paddingLeft: '20px', marginTop: '8px' }}>
          <li>ціна РДН і ВДР з oree.com.ua (Оператор ринку) — основний таргет прогнозу;</li>
          <li>погода Open-Meteo, перетворена на Solar_Gen/Wind_Gen через фізичну модель генерації;</li>
          <li>реальний транскордонний перетік електроенергії ENTSO-E (PL/RO/SK/HU/MD).</li>
        </ul>
        <p style={{ ...bodyStyle, marginTop: '8px' }}>
          Найвпливовіші ознаки моделі — авторегресійні лаги (вчорашня та тижнева ціна, спред ВДР-РДН). Продова
          модель — LightGBM; окремо рахується quantile-прогноз (P10/P90) для довірчого інтервалу невизначеності.
        </p>
      </div>

      <div className="glass-card" style={sectionGap}>
        <h3 className="card-title" style={cardTitleStyle}>Як рахується план диспетчеризації</h3>
        <p style={bodyStyle}>
          MILP-оптимізація (PuLP/CBC) шукає найприбутковіший погодинний графік заряд/розряд на прогнозних цінах,
          з урахуванням реальних лімітів батареї (ємність, потужність, максимум циклів на добу, межі SoC).
          Диспетчер може вручну скоригувати вхідні дані там, де модель сама цього не бачить: поправку доступності
          генерації АЕС/ГЕС/СЕС/ВЕС, ручний відсотковий зсув прогнозу ціни (для відомих тимчасових ринкових
          аномалій), початковий SoC на добу та «Відсоток буфера безпеки» для заявки.
        </p>
      </div>

      <div className="glass-card" style={sectionGap}>
        <h3 className="card-title" style={cardTitleStyle}>Щоденний автоматичний цикл системи</h3>
        <p style={bodyStyle}>
          Щодня о 06:00 система сама, без участі диспетчера: синхронізує реальні дані → рахує прогноз (LightGBM)
          та P10/P90-інтервал → рахує MILP-план → зберігає результат. Окремо, щоночі о 02:00, модель автоматично
          перенавчається на найсвіжіших даних.
        </p>
        <Callout>
          Ця джоба НІКОЛИ не займається заявками на ринок — усе, що стосується подачі та звірки заявок, диспетчер
          робить вручну (розділ нижче).
        </Callout>
        <Callout>
          Час «06:00» — це час контейнера сервера. Чи відповідає він 06:00 саме за київським часом (а не UTC) —
          наразі не перевірено остаточно; сприймати як орієнтир, а не гарантовану точку часу.
        </Callout>
      </div>

      <div className="glass-card" style={sectionGap}>
        <h3 className="card-title" style={cardTitleStyle}>
          Покрокова інструкція диспетчеру (щодня, поки немає автоподачі через API)
        </h3>
        <Step n={1}>
          Вранці (після 06:00) відкрити сторінку «Optimization Schedule» — переглянути рекомендований план,
          за потреби скоригувати «Відсоток буфера безпеки».
        </Step>
        <Step n={2}>
          Натиснути «Сформувати заявки зараз» (або «Зберегти маржу і сформувати заявки») — система порахує ціну
          заявки на кожну з 24 годин доби.
        </Step>
        <Step n={3}>
          Вручну перенести ці 24 пари обсяг/ціна в особистий кабінет oree.com.ua і подати заявки на РДН —{' '}
          <strong style={{ color: 'var(--text-primary)' }}>обов'язково до 12:00</strong> (закриття воріт РДН за
          офіційними правилами ринку). Краще подавати заздалегідь, не в останню хвилину.
        </Step>
        <Step n={4}>
          Після публікації реальної ціни РДН (зазвичай близько 13:00) — натиснути «Звірити з фактом OREE».
        </Step>
        <Step n={5}>
          Для годин, де заявка НЕ зіграла, система показує лише текстову підказку (орієнтовна ціна ВДР та
          прибуток) — без кнопки дії. Диспетчер сам вирішує і сам вручну подає заявку на ВДР у кабінеті OREE —{' '}
          <strong style={{ color: 'var(--text-primary)' }}>
            встигнути до закриття воріт ВДР, яке настає за 60 хвилин до кожної конкретної години постачання
          </strong>{' '}
          (ковзний дедлайн, не єдиний момент на добу). Чим раніше зроблена звірка — тим більше часу лишається на
          альтернативу для найближчих годин.
        </Step>
      </div>

      <div className="glass-card" style={sectionGap}>
        <h3 className="card-title" style={cardTitleStyle}>Поточний етап розробки</h3>
        <p style={bodyStyle}>
          <span className="status-badge online" style={{ padding: '2px 8px', fontSize: '0.7rem', marginRight: '8px' }}>реалізовано</span>
          прогноз ціни, MILP-план, ручні поправки, генерація заявок, звірка з фактом ринку, підказка по ВДР для
          несигравших годин, нічне автоматичне перенавчання моделі.
        </p>
        <p style={{ ...bodyStyle, marginTop: '10px' }}>
          <span className="status-badge offline" style={{ padding: '2px 8px', fontSize: '0.7rem', marginRight: '8px' }}>не реалізовано</span>
          автоматична подача та звірка заявок через API в кабінет OREE — усе це диспетчер поки робить вручну.
        </p>
      </div>

      <div className="glass-card">
        <h3 className="card-title" style={cardTitleStyle}>Подальші плани автоматизації</h3>
        <p style={bodyStyle}>
          За підсумками вивчення офіційних правил ринку OREE (Правила РДН/ВДР, затв. НКРЕКП) заплановано:
        </p>
        <ul style={{ ...bodyStyle, paddingLeft: '20px', marginTop: '8px' }}>
          <li>реєстрація учасником ринку (НКРЕКП, EIC-код, договори, escrow-рахунок, КЕП) — організаційна робота, не код;</li>
          <li>абстракція клієнта <code>oree_client</code> — спочатку заглушка, потім реальний REST/WebSocket-клієнт (OREE, ймовірно, використовує ту саму вендорську платформу XMtrade/ISOT, що й словацький оператор ринку OKTE, де такий API вже публічно задокументовано);</li>
          <li>розширення моделі заявки полями реальної подачі (ідентифікатор, статус, час);</li>
          <li>реальна автоподача заявок — з режимом dry-run за замовчуванням, щоб виключити випадкову реальну заявку на живий ринок;</li>
          <li>заміна текстової ВДР-підказки на реальне автоматичне виконання.</li>
        </ul>
        <Callout>
          Ринок двосторонніх договорів (РДД) навмисно НЕ використовується як миттєвий fallback для несигравших
          заявок. За офіційними правилами це довгостроковий механізм попереднього узгодження обсягів із
          контрагентом, а не спотова дія «заявка не зіграла → продай просто зараз кому завгодно» — для цього
          реалістичніший шлях це автоподача на ВДР.
        </Callout>
      </div>
    </div>
  );
}
